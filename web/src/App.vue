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
import { storeToRefs } from 'pinia';
import TopBar from './components/layout/TopBar.vue';
import SidebarNavigationContainer from './components/layout/SidebarNavigationContainer.vue';
import AgentChatContainer from './components/chat/AgentChatContainer.vue';
import PersonalityDetailsContainer from './components/personality/PersonalityDetailsContainer.vue';
import ChannelDetailsContainer from './components/channel/ChannelDetailsContainer.vue';
import { useAgentStore } from './stores/agentStore';
import { usePersonalityStore } from './stores/personalityStore';
import { useChannelStore } from './stores/channelStore';

const agentStore = useAgentStore();
const personalityStore = usePersonalityStore();
const channelStore = useChannelStore();
const { selectedAgentId } = storeToRefs(agentStore);
const { selectedPersonalityName } = storeToRefs(personalityStore);
const { selectedChannelKey } = storeToRefs(channelStore);

const handleSelectAgent = (agentId: string) => {
  personalityStore.setSelectedPersonalityName(null);
  channelStore.setSelectedChannelKey(null);
  agentStore.setSelectedAgentId(agentId);
};

const handleSelectPersonality = (personalityName: string) => {
  channelStore.setSelectedChannelKey(null);
  personalityStore.setSelectedPersonalityName(personalityName);
  agentStore.setSelectedAgentId(null);
};

const handleSelectChannel = (channelKey: string) => {
  personalityStore.setSelectedPersonalityName(null);
  agentStore.setSelectedAgentId(null);
  channelStore.setSelectedChannelKey(channelKey);
};
</script>

<template>
  <div class="flex flex-col h-screen w-full bg-slate-50 font-sans overflow-hidden">
    <TopBar />

    <div class="flex flex-1 overflow-hidden">
      <SidebarNavigationContainer
        @select-agent="handleSelectAgent"
        @select-personality="handleSelectPersonality"
        @select-channel="handleSelectChannel"
      />

        <!-- Main Content -->
        <div class="flex-1 flex flex-col relative min-w-0">
          <template v-if="selectedAgentId">
            <AgentChatContainer :agent-id="selectedAgentId" />
          </template>
          <template v-else-if="selectedPersonalityName">
            <PersonalityDetailsContainer :personality-name="selectedPersonalityName" />
          </template>
          <template v-else-if="selectedChannelKey">
            <ChannelDetailsContainer :channel-key="selectedChannelKey" />
          </template>
          <template v-else>
            <div class="flex-1 flex items-center justify-center bg-slate-50 text-slate-400">
            Select something to view.
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
