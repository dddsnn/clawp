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
import { fetchAgents, ChatConnection } from './services/api';
import { useAgentStore } from './stores/agentStore';
import { useChatStore } from './stores/chatStore';

const agentStore = useAgentStore();
const chatStore = useChatStore();
const { agents, selectedAgentId } = storeToRefs(agentStore);

const currentConnection = shallowRef<ChatConnection | null>(null);
const agentsLoading = ref(true);
const agentsError = ref<string | null>(null);

const handleSend = (text: string) => {
  currentConnection.value?.sendMessage(text);
};

onMounted(async () => {
  try {
    agentsLoading.value = true;
    agentsError.value = null;
    const fetchedAgents = await fetchAgents();
    agentStore.setAgents(fetchedAgents);
    if (fetchedAgents.length > 0) {
      agentStore.setSelectedAgentId(fetchedAgents[0].id);
    }
  } catch (error) {
    console.error('Failed to load agents:', error);
    agentsError.value = error instanceof Error ? error.message : String(error);
  } finally {
    agentsLoading.value = false;
  }
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
        @select-agent="agentStore.setSelectedAgentId"
      />

      <!-- Main Content -->
      <div class="flex-1 flex flex-col relative min-w-0">
        <template v-if="selectedAgentId">
          <ChatWindow @send="handleSend" />
        </template>
        <template v-else>
          <div class="flex-1 flex items-center justify-center bg-slate-50 text-slate-400">
            Select an agent to start chatting.
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
