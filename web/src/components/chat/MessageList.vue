<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import { useScroll } from '@vueuse/core';
import { useChatStore } from '../../stores/chatStore';
import MessageBubble from './MessageBubble.vue';
import { Bot } from 'lucide-vue-next';

const store = useChatStore();
const scrollContainer = ref<HTMLElement | null>(null);
const { y } = useScroll(scrollContainer, { behavior: 'smooth' });

// We want to auto-scroll if the user is near the bottom
const isNearBottom = ref(true);

const handleScroll = () => {
  if (!scrollContainer.value) return;
  const { scrollTop, scrollHeight, clientHeight } = scrollContainer.value;
  // Consider "near bottom" if within 100px
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 100;
};

// Use the new displayedMessages array
const filteredMessages = computed(() => {
  return store.displayedMessages.filter(msg => {
    if (msg.role === 'system') return store.visibility.system;
    if (msg.role === 'tool') return store.visibility.tool;
    if (msg.role === 'developer') return store.visibility.developer;
    return true; // Always show user and assistant messages
  });
});

// Auto-scroll when new messages arrive if we were already near the bottom
store.$subscribe(() => {
  if (isNearBottom.value && scrollContainer.value) {
    setTimeout(() => {
      if (scrollContainer.value) {
        y.value = scrollContainer.value.scrollHeight;
      }
    }, 50);
  }
});

onMounted(() => {
  if (scrollContainer.value) {
    y.value = scrollContainer.value.scrollHeight;
  }
});
</script>

<template>
  <div 
    ref="scrollContainer"
    @scroll="handleScroll"
    class="flex-1 overflow-y-auto p-4 md:p-8"
  >
    <div class="max-w-4xl mx-auto space-y-6">
      
      <!-- Empty State -->
      <div v-if="filteredMessages.length === 0" class="flex flex-col items-center justify-center h-64 text-slate-400 space-y-4">
        <Bot class="w-12 h-12 text-slate-300" />
        <p>No messages yet. Say hello!</p>
      </div>

      <!-- Message List -->
      <MessageBubble
        v-for="(msg, index) in filteredMessages"
        :key="msg.metadata?.seq_in_session ?? index"
        :message="msg"
      />
    </div>
  </div>
</template>
