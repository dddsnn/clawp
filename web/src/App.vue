<script setup lang="ts">
import { onMounted } from 'vue';
import TopBar from './components/layout/TopBar.vue';
import MessageList from './components/chat/MessageList.vue';
import ChatInput from './components/chat/ChatInput.vue';
import { MockAgentService } from './services/api';
import { useChatStore } from './stores/chatStore';

const apiService = new MockAgentService();

const handleSend = async (text: string) => {
  await apiService.sendMessage(text);
};

onMounted(() => {
  const store = useChatStore();
  store.addMessage({
    role: 'agent',
    content: 'Hello! I am your AI assistant. How can I help you today?',
  });
});
</script>

<template>
  <div class="flex flex-col h-screen w-full bg-slate-50 font-sans overflow-hidden">
    <TopBar />
    <MessageList />
    <ChatInput @send="handleSend" />
  </div>
</template>
