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
import { ref } from 'vue';
import { storeToRefs } from 'pinia';
import TopBar from './components/layout/TopBar.vue';
import SidebarNavigationContainer from './components/layout/SidebarNavigationContainer.vue';
import AgentChatContainer from './components/chat/AgentChatContainer.vue';
import PersonalityDetailsContainer from './components/personality/PersonalityDetailsContainer.vue';
import { useAgentStore } from './stores/agentStore';
import { usePersonalityStore } from './stores/personalityStore';

const agentStore = useAgentStore();
const personalityStore = usePersonalityStore();
const { selectedAgentId } = storeToRefs(agentStore);
const { selectedPersonalityName } = storeToRefs(personalityStore);

const activeSelection = ref<
  { type: 'agent'; id: string } |
  { type: 'personality'; name: string } |
  null
>(null);

const handleSelectAgent = (agentId: string) => {
  personalityStore.setSelectedPersonalityName(null);
  activeSelection.value = { type: 'agent', id: agentId };
  agentStore.setSelectedAgentId(agentId);
};

const handleSelectPersonality = (personalityName: string) => {
  personalityStore.setSelectedPersonalityName(personalityName);
  activeSelection.value = { type: 'personality', name: personalityName };
  agentStore.setSelectedAgentId(null);
};
</script>

<template>
  <div class="flex flex-col h-screen w-full bg-slate-50 font-sans overflow-hidden">
    <TopBar />

    <div class="flex flex-1 overflow-hidden">
      <SidebarNavigationContainer
        :active-selection-type="activeSelection?.type ?? null"
        @select-agent="handleSelectAgent"
        @select-personality="handleSelectPersonality"
      />

        <!-- Main Content -->
        <div class="flex-1 flex flex-col relative min-w-0">
          <template v-if="activeSelection?.type === 'agent' && selectedAgentId">
            <AgentChatContainer :agent-id="selectedAgentId" />
          </template>
          <template v-else-if="activeSelection?.type === 'personality' && selectedPersonalityName">
            <PersonalityDetailsContainer :personality-name="selectedPersonalityName" />
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
