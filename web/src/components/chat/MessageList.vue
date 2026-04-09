<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import MessageBubble from './MessageBubble.vue';
import { useChatStore } from '../../stores/chatStore';
import { storeToRefs } from 'pinia';
import { Bot } from 'lucide-vue-next';

const chatStore = useChatStore();
const { messages, visibility } = storeToRefs(chatStore);
const containerRef = ref<HTMLElement | null>(null);

// Auto-scroll logic
watch(() => messages.value, () => {
  // Check if we are at the bottom (or very close to it)
  // If so, scroll to bottom
  if (containerRef.value) {
    const el = containerRef.value;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    
    if (isAtBottom) {
      // Use nextTick or just a small timeout to ensure DOM is updated
      setTimeout(() => {
        el.scrollTop = el.scrollHeight;
      }, 50);
    }
  }
}, { deep: true });

const filteredMessages = computed(() => {
  return messages.value.filter(msg => {
    if (msg.role === 'system' && !visibility.value.system) return false;
    if (msg.role === 'tool' && !visibility.value.tool) return false;
    if (msg.role === 'developer' && !visibility.value.developer) return false;
    return true;
  });
});
</script>

<template>
  <div 
    ref="containerRef" 
    class="flex-1 overflow-y-auto px-4 py-6 md:px-8 space-y-6 scroll-smooth bg-slate-50"
  >
    <div class="max-w-4xl mx-auto w-full">
      <MessageBubble 
        v-for="(msg, idx) in filteredMessages" 
        :key="msg.metadata.seq_in_session ?? ((msg as any)._localId || idx)" 
        :message="msg" 
      />
      
      <div v-if="filteredMessages.length === 0" class="flex flex-col items-center justify-center h-full text-slate-400 mt-20">
        <Bot class="w-16 h-16 mb-4 text-slate-300" />
        <p class="text-lg">No messages yet. Start a conversation!</p>
      </div>
    </div>
  </div>
</template>
