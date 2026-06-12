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
import type { ReasoningVisibilityMode } from '../../stores/chatStore';

const props = defineProps<{
  message: Message | StreamingAssistantMessage;
  displayMode: 'full' | 'hint';
  reasoningVisibilityMode: ReasoningVisibilityMode;
}>();

const roleConfig = computed(() => {
  switch (props.message.role) {
    case 'user':
      return { icon: User, bgClass: 'bg-[var(--color-role-user-bg)] border-[var(--color-role-user-border)] text-[var(--color-role-user-text)]' };
    case 'agent':
      return { icon: Bot, bgClass: 'bg-[var(--color-role-agent-bg)] border-[var(--color-role-agent-border)] text-[var(--color-role-agent-text)]' };
    case 'system':
      return { icon: Server, bgClass: 'bg-[var(--color-role-developer-bg)] border-[var(--color-role-developer-border)] text-[var(--color-role-developer-text)]' };
    case 'tool':
      return { icon: Wrench, bgClass: 'bg-[var(--color-role-tool-bg)] border-[var(--color-role-tool-border)] text-[var(--color-role-tool-text)]' };
    case 'developer':
      return { icon: Terminal, bgClass: 'bg-[var(--color-role-developer-bg)] border-[var(--color-role-developer-border)] text-[var(--color-role-developer-text)]' };
    default:
      return { icon: User, bgClass: 'bg-white border-slate-200 text-slate-800' };
  }
});

const isAssistant = computed(() => props.message.role === 'agent');
const hasReasoning = computed(() => isAssistant.value && (props.message as any).reasoning);
const hasToolCalls = computed(() => isAssistant.value && (props.message as any).tool_calls && (props.message as any).tool_calls.length > 0);
const hasErrors = computed(() => isAssistant.value && (props.message as any).errors && (props.message as any).errors.length > 0);
const isHintMode = computed(() => props.displayMode === 'hint');

const isCrossChannelConversation = computed(() => {
  if (props.message.role !== 'user' && props.message.role !== 'agent') {
    return false;
  }

  return props.message.metadata.channel.type !== 'web_ui';
});

const channelTypeLabel = computed(() => {
  if (!isCrossChannelConversation.value) {
    return null;
  }

  return props.message.metadata.channel.type;
});

const hintPreview = computed(() => {
  const compact = props.message.content.trim().replace(/\s+/g, ' ');

  if (!compact) {
    return '(no content yet)';
  }

  return compact.length > 140 ? `${compact.slice(0, 140)}…` : compact;
});

const shouldRenderReasoning = computed(() => hasReasoning.value && props.reasoningVisibilityMode !== 'hide');
const shouldExpandReasoning = computed(() => props.reasoningVisibilityMode === 'expanded');

const formattedTime = computed(() => {
  if (!('time' in props.message.metadata)) return null;
  return props.message.metadata.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
});

</script>

<template>
  <div
    :class="[
      roleConfig.bgClass,
      'p-4 rounded-xl border shadow-sm w-full relative group pb-8 mb-6',
      isCrossChannelConversation ? 'saturate-50 opacity-85' : '',
    ]"
  >
    <component :is="isHintMode ? 'details' : 'div'" :class="isHintMode ? 'group/hint' : ''">
      <summary v-if="isHintMode" class="list-none [&::-webkit-details-marker]:hidden cursor-pointer select-none">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-2 min-w-0">
            <component :is="roleConfig.icon" class="w-4 h-4 shrink-0" />
            <span class="capitalize tracking-wide font-semibold text-sm">{{ message.role }}</span>
            <span v-if="channelTypeLabel" class="text-xs rounded-md px-1.5 py-0.5 bg-black/10 text-current uppercase tracking-wide">
              {{ channelTypeLabel }}
            </span>
            <span class="text-sm opacity-80 truncate">{{ hintPreview }}</span>
          </div>
          <ChevronDown class="w-4 h-4 shrink-0 transition-transform duration-200 group-open/hint:rotate-180" />
        </div>
      </summary>

      <div :class="isHintMode ? 'mt-3 pt-3 border-t border-black/10' : ''">
      <div class="flex items-center justify-between mb-2 font-medium">
        <div class="flex items-center space-x-2">
          <component :is="roleConfig.icon" class="w-5 h-5" />
          <span class="capitalize tracking-wide">{{ message.role }}</span>
          <span v-if="channelTypeLabel" class="text-xs rounded-md px-1.5 py-0.5 bg-black/10 text-current uppercase tracking-wide">
            {{ channelTypeLabel }}
          </span>
        </div>

        <details class="relative" v-if="message.metadata">
          <summary class="list-none [&::-webkit-details-marker]:hidden cursor-pointer p-1.5 rounded-md hover:bg-black/5 text-slate-400 hover:text-slate-600 transition-colors" title="View Metadata">
            <Braces class="w-4 h-4" />
          </summary>
          <div class="absolute right-0 top-full mt-1 z-10 w-80 bg-white border border-slate-200 rounded-lg shadow-lg p-3 text-xs text-slate-600 font-mono overflow-auto max-h-96">
            <pre class="whitespace-pre-wrap">{{ JSON.stringify(message.metadata, null, 2) }}</pre>
          </div>
        </details>
      </div>

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

      <details
        v-if="shouldRenderReasoning"
        :open="shouldExpandReasoning"
        class="mb-3 bg-white/50 border border-slate-300 rounded-lg overflow-hidden transition-all duration-300 group/reasoning"
      >
        <summary class="flex items-center space-x-2 p-3 cursor-pointer hover:bg-white/80 select-none text-sm font-medium text-slate-600">
          <ChevronDown class="w-4 h-4 transition-transform duration-300 group-open/reasoning:rotate-180" />
          <span>Reasoning</span>
        </summary>
        <div class="px-4 pb-4 pt-1 text-sm text-slate-700 font-mono whitespace-pre-wrap">
          {{ (message as any).reasoning }}
        </div>
      </details>

      <div class="text-base leading-relaxed whitespace-pre-wrap relative">
        {{ message.content }}
      </div>

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
      </div>
    </component>

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
