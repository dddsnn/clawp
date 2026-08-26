<!--
Copyright 2026 Marc Lehmann

This file is part of clawp.

clawp is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

clawp is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
details.

You should have received a copy of the GNU Affero General Public License along
with clawp. If not, see <https://www.gnu.org/licenses/>.
-->

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { X } from 'lucide-vue-next';
import { useRoute, useRouter } from 'vue-router';
import SidebarNavigation from './SidebarNavigation.vue';
import { useAgentStore } from '../../stores/agentStore';
import { usePersonalityStore } from '../../stores/personalityStore';
import { useChannelStore } from '../../stores/channelStore';
import { hatchAgent } from '../../services/api';
import type { ChannelInformation } from '../../types/api';

const agentStore = useAgentStore();
const personalityStore = usePersonalityStore();
const channelStore = useChannelStore();
const route = useRoute();
const router = useRouter();

const {
  agents,
  agentsLoading,
  agentsError,
} = storeToRefs(agentStore);

const {
  personalities,
  personalitiesLoading,
  personalitiesError,
} = storeToRefs(personalityStore);

const {
  channels,
  channelsLoading,
  channelsError,
} = storeToRefs(channelStore);

const isHatchModalOpen = ref(false);
const hatchPersonalityName = ref<string>('');
const hatchAgentName = ref<string>('');
const hatchError = ref<string | null>(null);
const isHatching = ref(false);

type Collection = 'root' | 'agents' | 'personalities' | 'channels';

const collection = computed<Collection>(() => route.meta.collection as Collection ?? 'root');
const selectedAgentId = computed(() => route.name === 'agent-chat' ? String(route.params.agentId) : null);
const selectedPersonalityName = computed(() => route.name === 'personality-details' ? String(route.params.personalityName) : null);
const selectedChannelKey = computed(() => route.name === 'channel-details'
  ? `${route.params.channelType}:${route.params.channelId}`
  : null);

const canHatch = computed(() => {
  return !personalitiesLoading.value && personalitiesError.value === null && personalities.value.length > 0;
});

const handleSelectAgent = (agentId: string) => {
  router.push({ name: 'agent-chat', params: { agentId } });
};

const handleSelectPersonality = (personalityName: string) => {
  router.push({ name: 'personality-details', params: { personalityName } });
};

const handleSelectChannel = (channel: ChannelInformation) => {
  if (channel.id === null) {
    return;
  }

  router.push({ name: 'channel-details', params: { channelType: channel.type, channelId: channel.id } });
};

const navigateCollection = (target: Exclude<Collection, 'root'>) => {
  router.push({ name: target });
};

const navigateBack = () => {
  const previousLocation = window.history.state?.back;
  if (typeof previousLocation === 'string' && previousLocation.startsWith('/')) {
    router.back();
    return;
  }

  if (route.name === 'agents' || route.name === 'personalities' || route.name === 'channels') {
    router.push({ name: 'home' });
    return;
  }

  router.push({ name: collection.value });
};

const openHatchModal = () => {
  if (!canHatch.value) {
    return;
  }

  hatchError.value = null;
  hatchPersonalityName.value = personalities.value[0]?.name ?? '';
  hatchAgentName.value = '';
  isHatchModalOpen.value = true;
};

const closeHatchModalInternal = (force = false) => {
  if (isHatching.value && !force) {
    return;
  }

  isHatchModalOpen.value = false;
  hatchError.value = null;
  hatchPersonalityName.value = '';
  hatchAgentName.value = '';
};

const closeHatchModal = () => {
  closeHatchModalInternal(false);
};

const submitHatchAgent = async () => {
  const agentName = hatchAgentName.value.trim();
  if (!hatchPersonalityName.value || !agentName || isHatching.value) {
    return;
  }

  isHatching.value = true;
  hatchError.value = null;

  try {
    const newAgent = await hatchAgent(agentName, hatchPersonalityName.value);
    agentStore.addAgent(newAgent);
    closeHatchModalInternal(true);
    router.push({ name: 'agent-chat', params: { agentId: newAgent.id } });
  } catch (error) {
    console.error('Failed to hatch agent:', error);
    hatchError.value = error instanceof Error ? error.message : String(error);
  } finally {
    isHatching.value = false;
  }
};

onMounted(async () => {
  await Promise.allSettled([
    agentStore.fetchAgents(),
    personalityStore.fetchPersonalities(),
    channelStore.fetchChannels(),
  ]);
});

</script>

<template>
  <SidebarNavigation
    :collection="collection"
    :agents="agents"
    :selected-agent-id="selectedAgentId"
    :agents-loading="agentsLoading"
    :agents-error="agentsError"
    :personalities="personalities"
    :selected-personality-name="selectedPersonalityName"
    :personalities-loading="personalitiesLoading"
    :personalities-error="personalitiesError"
    :channels="channels"
    :selected-channel-key="selectedChannelKey"
    :channels-loading="channelsLoading"
    :channels-error="channelsError"
    :can-hatch="canHatch"
    :is-hatching="isHatching"
    @navigate-collection="navigateCollection"
    @navigate-back="navigateBack"
    @select-agent="handleSelectAgent"
    @select-personality="handleSelectPersonality"
    @select-channel="handleSelectChannel"
    @open-hatch-modal="openHatchModal"
  />

  <div v-if="isHatchModalOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/45 p-4">
    <div class="w-full max-w-md rounded-xl border border-slate-200 bg-white shadow-xl">
      <div class="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <h3 class="text-sm font-semibold text-slate-800">Hatch New Agent</h3>
        <button
          class="rounded-md p-1 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50"
          :disabled="isHatching"
          @click="closeHatchModal"
          title="Close"
        >
          <X class="h-4 w-4" />
        </button>
      </div>

      <div class="space-y-3 px-4 py-4">
        <div>
          <label class="block text-sm font-medium text-slate-700" for="hatch-agent-name">Name</label>
          <input
            id="hatch-agent-name"
            v-model="hatchAgentName"
            type="text"
            class="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            :disabled="isHatching"
            required
          />
        </div>

        <div>
        <label class="block text-sm font-medium text-slate-700" for="hatch-personality">Personality</label>
        <select
          id="hatch-personality"
          v-model="hatchPersonalityName"
          class="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
          :disabled="isHatching"
        >
          <option value="" disabled>Select a personality</option>
          <option v-for="personality in personalities" :key="personality.name" :value="personality.name">
            {{ personality.name }}
          </option>
        </select>
        </div>

        <p v-if="hatchError" class="text-sm text-red-600">
          Failed to hatch agent: {{ hatchError }}
        </p>
      </div>

      <div class="flex items-center justify-end gap-2 border-t border-slate-100 px-4 py-3">
        <button
          class="rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="isHatching"
          @click="closeHatchModal"
        >
          Cancel
        </button>
        <button
          class="inline-flex items-center rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
          :disabled="!hatchPersonalityName || !hatchAgentName.trim() || isHatching"
          @click="submitHatchAgent"
        >
          <span v-if="isHatching">Hatching...</span>
          <span v-else>Hatch Agent</span>
        </button>
      </div>
    </div>
  </div>
</template>
