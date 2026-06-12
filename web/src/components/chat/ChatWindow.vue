<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { Eye, EyeOff, Loader2, Minimize2, WifiOff } from 'lucide-vue-next';
import { useChatStore, type MessageVisibilityMode, type ReasoningVisibilityMode } from '../../stores/chatStore';
import MessageList from './MessageList.vue';
import ChatInput from './ChatInput.vue';

const emit = defineEmits<{
  (e: 'send', message: string): void
}>();

const chatStore = useChatStore();
const { connectionState, visibility } = storeToRefs(chatStore);

const messageFilters: Array<{ key: 'systemDeveloper' | 'tool' | 'crossChannelConversation'; label: string }> = [
  { key: 'systemDeveloper', label: 'System / Developer' },
  { key: 'tool', label: 'Tool' },
  { key: 'crossChannelConversation', label: 'Other-channel user/agent' },
];

const modeToLabel: Record<MessageVisibilityMode, string> = {
  show: 'Show',
  hint: 'Hint',
  hide: 'Hide',
};

const modeToIcon = {
  show: Eye,
  hint: Minimize2,
  hide: EyeOff,
};

const modeToClass: Record<MessageVisibilityMode, string> = {
  show: 'bg-white text-slate-800 border-slate-300',
  hint: 'bg-amber-50 text-amber-800 border-amber-200',
  hide: 'bg-slate-100 text-slate-400 hover:text-slate-600 border-slate-200',
};

const reasoningModeToClass: Record<ReasoningVisibilityMode, string> = {
  hide: 'bg-slate-100 text-slate-500 border-slate-200',
  collapsed: 'bg-indigo-50 text-indigo-800 border-indigo-200',
  expanded: 'bg-indigo-100 text-indigo-900 border-indigo-300',
};

const handleSend = (text: string) => {
  emit('send', text);
};

const handleCycleMessageVisibility = (key: 'systemDeveloper' | 'tool' | 'crossChannelConversation') => {
  chatStore.cycleMessageVisibility(key);
};

const handleCycleReasoningVisibility = () => {
  chatStore.cycleReasoningVisibility();
};
</script>

<template>
    <div class="flex flex-col z-10 sticky top-0">
    <!-- Connection Status Banner -->
    <div v-if="connectionState.status === 'disconnected'" class="bg-slate-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
        <WifiOff class="w-4 h-4" />
        <span>Disconnected from chat.</span>
    </div>
    <div v-else-if="connectionState.status === 'connecting' && connectionState.error" class="bg-red-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
        <Loader2 class="w-4 h-4 animate-spin" />
        <span>Error: {{ connectionState.error }}. Reconnecting... (Attempt {{ connectionState.attempt }})</span>
    </div>
    <div v-else-if="connectionState.status === 'connecting'" class="bg-blue-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
        <Loader2 class="w-4 h-4 animate-spin" />
        <span>Connecting to chat... (Attempt {{ connectionState.attempt }})</span>
    </div>
    </div>
  <div class="flex flex-row flex-1 overflow-hidden w-full relative">
    <!-- Main Chat Area -->
    <div class="flex flex-col flex-1 min-w-0 h-full border-r border-slate-200">
      <MessageList />
      <ChatInput @send="handleSend" />
    </div>

    <!-- Right Sidebar / Settings Pane -->
    <aside class="w-auto bg-slate-50 flex flex-col items-stretch py-4 px-3 space-y-4 shadow-inner overflow-y-auto border-l border-slate-200">
      <div
        v-for="filter in messageFilters"
        :key="filter.key"
        class="group relative flex justify-center w-full"
      >
        <button
          @click="handleCycleMessageVisibility(filter.key)"
          class="px-4 py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-start border space-x-2 w-full"
          :class="modeToClass[visibility[filter.key]]"
          :aria-label="`Cycle ${filter.label} visibility mode`"
          :title="`${filter.label}: ${modeToLabel[visibility[filter.key]]} (click to cycle)`"
        >
          <component :is="modeToIcon[visibility[filter.key]]" class="w-5 h-5 shrink-0" />
          <span class="font-medium text-sm">{{ filter.label }}</span>
          <span class="ml-auto text-xs font-semibold uppercase tracking-wide">{{ modeToLabel[visibility[filter.key]] }}</span>
        </button>
      </div>

      <div class="group relative flex justify-center w-full">
        <button
          @click="handleCycleReasoningVisibility"
          class="px-4 py-2.5 rounded-xl transition-all shadow-sm flex items-center justify-start border space-x-2 w-full"
          :class="reasoningModeToClass[visibility.reasoning]"
          :aria-label="'Cycle reasoning visibility mode'"
          :title="`Reasoning: ${visibility.reasoning} (click to cycle)`"
        >
          <span class="font-medium text-sm">Reasoning</span>
          <span class="ml-auto text-xs font-semibold uppercase tracking-wide">{{ visibility.reasoning }}</span>
        </button>
      </div>
    </aside>
  </div>
</template>
