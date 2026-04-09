<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import TopBar from './components/layout/TopBar.vue';
import MessageList from './components/chat/MessageList.vue';
import ChatInput from './components/chat/ChatInput.vue';
import { ApiService } from './services/api';

const apiService = new ApiService();

const handleSend = async (text: string) => {
  await apiService.sendMessage(text);
};

onMounted(() => {
  apiService.init();
});

onUnmounted(() => {
  // If we wanted to cleanly close the websocket, we'd do it here.
});
</script>

<template>
  <div class="flex flex-col h-screen w-full bg-slate-50 font-sans overflow-hidden">
    <TopBar />
    <MessageList />
    <ChatInput @send="handleSend" />
  </div>
</template>
