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
import { onMounted, onUnmounted, watch, shallowRef, ref } from 'vue';
import { storeToRefs } from 'pinia';
import TopBar from './components/layout/TopBar.vue';
import SidebarNavigation from './components/layout/SidebarNavigation.vue';
import ChatWindow from './components/chat/ChatWindow.vue';
import PersonalityDetails from './components/personality/PersonalityDetails.vue';
import { fetchAgents, fetchPersonalities, fetchPersonality, ChatConnection } from './services/api';
import { useAgentStore } from './stores/agentStore';
import { useChatStore } from './stores/chatStore';
import type { AgentPersonality, AgentPersonalityWithFileContents } from './types/api';

const agentStore = useAgentStore();
const chatStore = useChatStore();
const { agents, selectedAgentId } = storeToRefs(agentStore);

const currentConnection = shallowRef<ChatConnection | null>(null);
const agentsLoading = ref(true);
const agentsError = ref<string | null>(null);
const personalities = ref<AgentPersonality[]>([]);
const personalitiesLoading = ref(true);
const personalitiesError = ref<string | null>(null);
const selectedPersonalityName = ref<string | null>(null);
const personalityDetails = ref<AgentPersonalityWithFileContents | null>(null);
const personalityDetailsLoading = ref(false);
const personalityDetailsError = ref<string | null>(null);
const personalityRequestCounter = ref(0);

const activeSelection = ref<
  { type: 'agent'; id: string } |
  { type: 'personality'; name: string } |
  null
>(null);

const handleSend = (text: string) => {
  currentConnection.value?.sendMessage(text);
};

const loadAgents = async () => {
  try {
    agentsLoading.value = true;
    agentsError.value = null;
    const fetchedAgents = await fetchAgents();
    agentStore.setAgents(fetchedAgents);
    if (fetchedAgents.length > 0) {
      agentStore.setSelectedAgentId(fetchedAgents[0].id);
      activeSelection.value = { type: 'agent', id: fetchedAgents[0].id };
    }
  } catch (error) {
    console.error('Failed to load agents:', error);
    agentsError.value = error instanceof Error ? error.message : String(error);
  } finally {
    agentsLoading.value = false;
  }
};

const loadPersonalities = async () => {
  try {
    personalitiesLoading.value = true;
    personalitiesError.value = null;
    personalities.value = await fetchPersonalities();
  } catch (error) {
    console.error('Failed to load personalities:', error);
    personalitiesError.value = error instanceof Error ? error.message : String(error);
  } finally {
    personalitiesLoading.value = false;
  }
};

const loadPersonalityDetails = async (personalityName: string) => {
  const requestId = ++personalityRequestCounter.value;

  personalityDetailsLoading.value = true;
  personalityDetailsError.value = null;
  personalityDetails.value = null;

  try {
    const details = await fetchPersonality(personalityName);
    if (requestId !== personalityRequestCounter.value) {
      return;
    }
    personalityDetails.value = details;
  } catch (error) {
    if (requestId !== personalityRequestCounter.value) {
      return;
    }
    console.error(`Failed to load personality ${personalityName}:`, error);
    personalityDetailsError.value = error instanceof Error ? error.message : String(error);
  } finally {
    if (requestId === personalityRequestCounter.value) {
      personalityDetailsLoading.value = false;
    }
  }
};

const handleSelectAgent = (agentId: string) => {
  selectedPersonalityName.value = null;
  activeSelection.value = { type: 'agent', id: agentId };
  agentStore.setSelectedAgentId(agentId);
};

const handleSelectPersonality = async (personalityName: string) => {
  selectedPersonalityName.value = personalityName;
  activeSelection.value = { type: 'personality', name: personalityName };
  agentStore.setSelectedAgentId(null);
  await loadPersonalityDetails(personalityName);
};

onMounted(async () => {
  await Promise.allSettled([loadAgents(), loadPersonalities()]);
});

watch(selectedAgentId, (newId) => {
  if (currentConnection.value) {
    currentConnection.value.disconnect();
    currentConnection.value = null;
  }

  if (newId) {
    currentConnection.value = new ChatConnection(newId);
    currentConnection.value.connect();
  } else {
    chatStore.clearMessages();
    chatStore.setConnectionState({ status: 'uninitialized' });
  }
});

onUnmounted(() => {
  if (currentConnection.value) {
    currentConnection.value.disconnect();
  }
});
</script>

<template>
  <div class="flex flex-col h-screen w-full bg-slate-50 font-sans overflow-hidden">
    <TopBar />

    <div class="flex flex-1 overflow-hidden">
      <SidebarNavigation
        :agents="agents"
        :selected-agent-id="selectedAgentId"
        :agents-loading="agentsLoading"
        :agents-error="agentsError"
        :personalities="personalities"
        :selected-personality-name="selectedPersonalityName"
        :personalities-loading="personalitiesLoading"
        :personalities-error="personalitiesError"
        :active-selection-type="activeSelection?.type ?? null"
        @select-agent="handleSelectAgent"
        @select-personality="handleSelectPersonality"
      />

      <!-- Main Content -->
      <div class="flex-1 flex flex-col relative min-w-0">
        <template v-if="activeSelection?.type === 'agent' && selectedAgentId">
          <ChatWindow @send="handleSend" />
        </template>
        <template v-else-if="activeSelection?.type === 'personality'">
          <PersonalityDetails
            :selected-personality-name="selectedPersonalityName"
            :is-loading="personalityDetailsLoading"
            :error="personalityDetailsError"
            :personality="personalityDetails"
          />
        </template>
        <template v-else>
          <div class="flex-1 flex items-center justify-center bg-slate-50 text-slate-400">
            Select an agent or personality.
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
