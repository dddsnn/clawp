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
import { computed, onMounted, watch } from 'vue';
import { storeToRefs } from 'pinia';
import SidebarNavigation from './SidebarNavigation.vue';
import { useAgentStore } from '../../stores/agentStore';
import { usePersonalityStore } from '../../stores/personalityStore';

const emit = defineEmits<{
  selectAgent: [id: string];
  selectPersonality: [name: string];
}>();

const agentStore = useAgentStore();
const personalityStore = usePersonalityStore();

const {
  agents,
  selectedAgentId,
  agentsLoading,
  agentsError,
} = storeToRefs(agentStore);

const {
  personalities,
  selectedPersonalityName,
  personalitiesLoading,
  personalitiesError,
} = storeToRefs(personalityStore);

const activeSelectionType = computed<'agent' | 'personality' | null>(() => {
  if (selectedAgentId.value) {
    return 'agent';
  }

  if (selectedPersonalityName.value) {
    return 'personality';
  }

  return null;
});

const handleSelectAgent = (agentId: string) => {
  emit('selectAgent', agentId);
};

const handleSelectPersonality = (personalityName: string) => {
  emit('selectPersonality', personalityName);
};

onMounted(async () => {
  await Promise.allSettled([
    agentStore.fetchAgents(),
    personalityStore.fetchPersonalities(),
  ]);
});

watch(
  [agents, agentsLoading, agentsError, activeSelectionType],
  ([loadedAgents, loadingAgents, loadingError, activeType]) => {
    if (activeType !== null || loadingAgents || loadingError || loadedAgents.length === 0) {
      return;
    }

    emit('selectAgent', loadedAgents[0].id);
  },
  { immediate: true },
);
</script>

<template>
  <SidebarNavigation
    :agents="agents"
    :selected-agent-id="selectedAgentId"
    :agents-loading="agentsLoading"
    :agents-error="agentsError"
    :personalities="personalities"
    :selected-personality-name="selectedPersonalityName"
    :personalities-loading="personalitiesLoading"
    :personalities-error="personalitiesError"
    :active-selection-type="activeSelectionType"
    @select-agent="handleSelectAgent"
    @select-personality="handleSelectPersonality"
  />
</template>
