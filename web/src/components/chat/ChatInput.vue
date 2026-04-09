<script setup lang="ts">
import { ref } from 'vue';
import { useTextareaAutosize } from '@vueuse/core';
import { SendHorizontal } from 'lucide-vue-next';

const emit = defineEmits<{
  (e: 'send', message: string): void
}>();

const { textarea, input } = useTextareaAutosize();
const isSubmitting = ref(false);

const handleKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
};

const sendMessage = () => {
  const text = input.value?.trim();
  if (!text || isSubmitting.value) return;

  emit('send', text);
  input.value = ''; // Reset input
  // Small hack to force reset size
  if (textarea.value) {
    textarea.value.style.height = 'auto';
  }
};
</script>

<template>
  <div class="p-4 bg-white border-t shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)] relative z-10">
    <div class="max-w-4xl mx-auto flex items-end gap-2 relative">
      <textarea
        ref="textarea"
        v-model="input"
        @keydown="handleKeydown"
        placeholder="Type a message... (Shift+Enter for new line)"
        class="w-full bg-slate-100 border border-slate-300 rounded-xl px-4 py-3 min-h-[48px] max-h-[200px] resize-none focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 transition-all text-slate-800 placeholder-slate-400"
        rows="1"
      ></textarea>
      
      <button 
        @click="sendMessage"
        :disabled="!input?.trim()"
        class="p-3 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm shrink-0 flex items-center justify-center h-[48px] w-[48px]"
        aria-label="Send message"
      >
        <SendHorizontal class="w-5 h-5" />
      </button>
    </div>
    <div class="max-w-4xl mx-auto mt-2 text-center">
      <span class="text-xs text-slate-400">Press Enter to send, Shift+Enter for new line</span>
    </div>
  </div>
</template>
