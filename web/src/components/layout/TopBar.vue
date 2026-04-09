<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useChatStore } from '../../stores/chatStore';
import { Eye, EyeOff } from 'lucide-vue-next';

const chatStore = useChatStore();
const { visibility } = storeToRefs(chatStore);
</script>

<template>
  <header class="flex items-center justify-between px-4 py-3 bg-white border-b shadow-sm z-10 sticky top-0">
    <div class="flex items-center space-x-2">
      <h1 class="text-xl font-semibold text-slate-800 tracking-tight">AI Assistant</h1>
    </div>

    <div class="flex items-center space-x-4 bg-slate-100 p-1.5 rounded-lg border border-slate-200 shadow-inner">
      <button 
        v-for="(val, key) in visibility" 
        :key="key"
        @click="chatStore.toggleVisibility(key as any)"
        class="flex items-center space-x-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
        :class="[val ? 'bg-white shadow-sm text-slate-900 border border-slate-200' : 'text-slate-500 hover:text-slate-700']"
      >
        <component :is="val ? Eye : EyeOff" class="w-4 h-4" />
        <span class="capitalize">{{ key }}</span>
      </button>
    </div>
  </header>
</template>
