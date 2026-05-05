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
import { computed } from 'vue';
import type { Message, StreamingAssistantMessage } from '../../types/api';
import { User, Bot, Server, Wrench, Terminal, ChevronDown, Braces, AlertCircle, Loader2 } from 'lucide-vue-next';

const props = defineProps<{ message: Message | StreamingAssistantMessage }>();

const roleConfig = computed(() => {
  switch (props.message.role) {
    case 'user':
      return { icon: User, bgClass: 'bg-[var(--color-role-user-bg)] border-[var(--color-role-user-border)] text-[var(--color-role-user-text)]' };
    case 'assistant':
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

const isAssistant = computed(() => props.message.role === 'assistant');
const hasReasoning = computed(() => isAssistant.value && (props.message as any).reasoning);
const hasToolCalls = computed(() => isAssistant.value && (props.message as any).tool_calls && (props.message as any).tool_calls.length > 0);
const hasErrors = computed(() => isAssistant.value && (props.message as any).errors && (props.message as any).errors.length > 0);

const formattedTime = computed(() => {
  if (!('time' in props.message.metadata)) return null;
  return props.message.metadata.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
});

</script>

<template>
  <div :class="[roleConfig.bgClass, 'p-4 rounded-xl border shadow-sm w-full relative group pb-8 mb-6']">
    <div class="flex items-center justify-between mb-2 font-medium">
      <div class="flex items-center space-x-2">
        <component :is="roleConfig.icon" class="w-5 h-5" />
        <span class="capitalize tracking-wide">{{ message.role }}</span>
      </div>

      <!-- Metadata Button / Popup -->
      <details class="relative" v-if="message.metadata">
        <summary class="list-none [&::-webkit-details-marker]:hidden cursor-pointer p-1.5 rounded-md hover:bg-black/5 text-slate-400 hover:text-slate-600 transition-colors" title="View Metadata">
          <Braces class="w-4 h-4" />
        </summary>
        <div class="absolute right-0 top-full mt-1 z-10 w-80 bg-white border border-slate-200 rounded-lg shadow-lg p-3 text-xs text-slate-600 font-mono overflow-auto max-h-96">
          <pre class="whitespace-pre-wrap">{{ JSON.stringify(message.metadata, null, 2) }}</pre>
        </div>
      </details>
    </div>

    <!-- Errors -->
    <div v-if="hasErrors" class="mb-4 bg-[var(--color-role-error-bg)] border-[var(--color-role-error-border)] rounded-lg overflow-hidden shadow-sm">
      <div class="flex items-center space-x-2 p-3 bg-[var(--color-role-error-bg-dark)] border-b border-[var(--color-role-error-border)] text-sm font-medium text-[var(--color-role-error-text)]">
        <AlertCircle class="w-4 h-4" />
        <span>Errors ({{ (message as any).errors.length }})</span>
      </div>
      <div class="px-4 py-3 text-sm text-[var(--color-role-error-text)] font-mono whitespace-pre-wrap divide-y divide-[var(--color-role-error-bg-dark)]">
        <div v-for="(err, idx) in (message as any).errors" :key="idx" class="py-1 first:pt-0 last:pb-0">
          {{ err }}
        </div>
      </div>
    </div>

    <!-- Reasoning / Thought block -->
    <details v-if="hasReasoning" class="mb-3 bg-white/50 border border-slate-300 rounded-lg overflow-hidden transition-all duration-300 group/reasoning">
      <summary class="flex items-center space-x-2 p-3 cursor-pointer hover:bg-white/80 select-none text-sm font-medium text-slate-600">
        <ChevronDown class="w-4 h-4 transition-transform duration-300 group-open/reasoning:rotate-180" />
        <span>Thought</span>
      </summary>
      <div class="px-4 pb-4 pt-1 text-sm text-slate-700 font-mono whitespace-pre-wrap">
        {{ (message as any).reasoning }}
      </div>
    </details>

    <!-- Main Content -->
    <div class="text-base leading-relaxed whitespace-pre-wrap relative">
      {{ message.content }}
    </div>

    <!-- Tool Calls -->
    <details v-if="hasToolCalls" class="mt-4 bg-white/50 border border-slate-300 rounded-lg overflow-hidden group/tools">
      <summary class="flex items-center space-x-2 p-3 cursor-pointer hover:bg-white/80 select-none text-sm font-medium text-slate-600">
        <Wrench class="w-4 h-4" />
        <span>Tool Calls ({{ (message as any).tool_calls.length }})</span>
        <ChevronDown class="w-4 h-4 transition-transform duration-300 group-open/tools:rotate-180 ml-auto" />
      </summary>
      <div class="px-4 pb-4 pt-1 text-xs text-slate-700 font-mono bg-slate-50/50">
        <pre class="overflow-x-auto">{{ JSON.stringify((message as any).tool_calls, null, 2) }}</pre>
      </div>
    </details>

    <!-- Timestamp / Loading indicator -->
    <span class="absolute bottom-2 right-4 text-[10px] text-slate-400 font-medium select-none flex items-center">
      <template v-if="formattedTime">
        {{ formattedTime }}
      </template>
      <template v-else>
        <Loader2 class="w-3 h-3 animate-spin text-slate-400" />
      </template>
    </span>
  </div>
</template>
