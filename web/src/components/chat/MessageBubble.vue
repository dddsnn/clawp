<script setup lang="ts">
import { computed } from 'vue';
import type { Message } from '../../types/chat';
import { User, Bot, Server, Wrench, Terminal, ChevronDown, Braces } from 'lucide-vue-next';

const props = defineProps<{ message: Message }>();

const roleConfig = computed(() => {
  switch (props.message.role) {
    case 'user':
      return { icon: User, bgClass: 'bg-[var(--color-role-user-bg)] border-[var(--color-role-user-border)] text-[var(--color-role-user-text)]' };
    case 'agent':
      return { icon: Bot, bgClass: 'bg-[var(--color-role-agent-bg)] border-[var(--color-role-agent-border)] text-[var(--color-role-agent-text)]' };
    case 'system':
      return { icon: Server, bgClass: 'bg-[var(--color-role-system-bg)] border-[var(--color-role-system-border)] text-[var(--color-role-system-text)]' };
    case 'tool':
      return { icon: Wrench, bgClass: 'bg-[var(--color-role-tool-bg)] border-[var(--color-role-tool-border)] text-[var(--color-role-tool-text)]' };
    case 'developer':
      return { icon: Terminal, bgClass: 'bg-[var(--color-role-developer-bg)] border-[var(--color-role-developer-border)] text-[var(--color-role-developer-text)]' };
    default:
      return { icon: User, bgClass: 'bg-white border-slate-200 text-slate-800' };
  }
});
</script>

<template>
  <div :class="[roleConfig.bgClass, 'p-4 rounded-xl border shadow-sm mb-4 w-full']">
    <div class="flex items-center space-x-2 mb-2 font-medium">
      <component :is="roleConfig.icon" class="w-5 h-5" />
      <span class="capitalize tracking-wide">{{ message.role }}</span>
    </div>

    <!-- Reasoning / Thought block -->
    <details v-if="message.reasoning" class="group mb-3 bg-white/50 border border-slate-300 rounded-lg overflow-hidden transition-all duration-300">
      <summary class="flex items-center space-x-2 p-3 cursor-pointer hover:bg-white/80 select-none text-sm font-medium text-slate-600">
        <ChevronDown class="w-4 h-4 transition-transform duration-300 group-open:rotate-180" />
        <span>Thought</span>
      </summary>
      <div class="px-4 pb-4 pt-1 text-sm text-slate-700 font-mono whitespace-pre-wrap">
        {{ message.reasoning }}
      </div>
    </details>

    <!-- Main Content -->
    <div class="text-base leading-relaxed whitespace-pre-wrap">
      {{ message.content }}
    </div>

    <!-- Tool Calls -->
    <details v-if="message.tool_calls && message.tool_calls.length > 0" class="group mt-4 bg-white/50 border border-slate-300 rounded-lg overflow-hidden">
      <summary class="flex items-center space-x-2 p-3 cursor-pointer hover:bg-white/80 select-none text-sm font-medium text-slate-600">
        <Wrench class="w-4 h-4" />
        <span>Tool Calls ({{ message.tool_calls.length }})</span>
        <ChevronDown class="w-4 h-4 transition-transform duration-300 group-open:rotate-180 ml-auto" />
      </summary>
      <div class="px-4 pb-4 pt-1 text-xs text-slate-700 font-mono bg-slate-50/50">
        <pre class="overflow-x-auto">{{ JSON.stringify(message.tool_calls, null, 2) }}</pre>
      </div>
    </details>

    <!-- Metadata Dropdown -->
    <details class="group mt-4 bg-white border border-slate-200 rounded-lg overflow-hidden shadow-sm" v-if="message.metadata">
      <summary class="flex items-center space-x-2 px-3 py-2 cursor-pointer hover:bg-slate-50 select-none text-xs font-semibold text-slate-500 uppercase tracking-wider">
        <Braces class="w-3.5 h-3.5" />
        <span>Metadata</span>
        <ChevronDown class="w-3.5 h-3.5 transition-transform duration-300 group-open:rotate-180 ml-auto" />
      </summary>
      <div class="p-3 text-xs text-slate-600 font-mono bg-slate-50 border-t border-slate-200">
        <pre class="overflow-x-auto">{{ JSON.stringify(message.metadata, null, 2) }}</pre>
      </div>
    </details>
  </div>
</template>
