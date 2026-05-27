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
import ChatWindow from './components/chat/ChatWindow.vue';
import { fetchAgents, ChatConnection } from './services/api';
import { useAgentStore } from './stores/agentStore';
import { useChatStore } from './stores/chatStore';
import { Users, Loader2, AlertCircle } from 'lucide-vue-next';

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
      <!-- Left Navigation Pane -->
      <aside class="w-64 bg-white border-r border-slate-200 flex flex-col flex-shrink-0 z-10">
        <div class="p-4 border-b border-slate-100 bg-slate-50/50 flex items-center space-x-2">
          <Users class="w-5 h-5 text-slate-500" />
          <h2 class="text-sm font-semibold text-slate-700 tracking-wide uppercase">Agents</h2>
        </div>

        <div class="flex-1 overflow-y-auto p-2 space-y-1">
          <div v-if="agentsLoading" class="flex flex-col items-center justify-center p-4 space-y-2 text-slate-400">
            <Loader2 class="w-6 h-6 animate-spin" />
            <span class="text-xs">Loading agents...</span>
          </div>

          <div v-else-if="agentsError" class="flex flex-col items-center justify-center p-4 space-y-2 text-red-400">
            <AlertCircle class="w-6 h-6 text-red-400" />
            <span class="text-xs text-center text-red-500">Failed to load agents<br><span class="opacity-80">{{ agentsError }}</span></span>
          </div>

          <div v-else-if="agents.length === 0" class="text-sm text-slate-400 p-4 text-center">
            No agents available.
          </div>

          <template v-else>
            <button
              v-for="agent in agents"
              :key="agent.id"
              @click="agentStore.setSelectedAgentId(agent.id)"
              class="w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all duration-200 truncate font-mono"
              :class="[
                selectedAgentId === agent.id
                  ? 'bg-blue-50 text-blue-700 font-medium shadow-sm ring-1 ring-blue-500/20'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
              ]"
              :title="agent.id"
            >
              {{ agent.id }}
            </button>
          </template>
        </div>
      </aside>

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
