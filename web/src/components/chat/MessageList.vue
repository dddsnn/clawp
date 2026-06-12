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
import { computed, ref, onMounted } from 'vue';
import { useScroll } from '@vueuse/core';
import { useChatStore } from '../../stores/chatStore';
import type { Message, StreamingAssistantMessage } from '../../types/api';
import MessageBubble from './MessageBubble.vue';
import { Bot, Loader2, AlertCircle } from 'lucide-vue-next';

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

type BubbleDisplayMode = 'full' | 'hint';

const isCrossChannelConversationMessage = (message: Message | StreamingAssistantMessage): boolean => {
  if (message.role !== 'user' && message.role !== 'agent') {
    return false;
  }

  return message.metadata.channel.type !== 'web_ui';
};

const resolveDisplayMode = (message: Message | StreamingAssistantMessage): BubbleDisplayMode | 'hidden' => {
  if (message.role === 'system' || message.role === 'developer') {
    const mode = store.visibility.systemDeveloper;
    if (mode === 'hide') return 'hidden';
    if (mode === 'hint') return 'hint';
    return 'full';
  }

  if (message.role === 'tool') {
    const mode = store.visibility.tool;
    if (mode === 'hide') return 'hidden';
    if (mode === 'hint') return 'hint';
    return 'full';
  }

  if (isCrossChannelConversationMessage(message)) {
    const mode = store.visibility.crossChannelConversation;
    if (mode === 'hide') return 'hidden';
    if (mode === 'hint') return 'hint';
    return 'full';
  }

  return 'full';
};

const presentedMessages = computed(() => {
  return store.displayedMessages
    .map((message) => ({
      message,
      displayMode: resolveDisplayMode(message),
    }))
    .filter((entry) => entry.displayMode !== 'hidden') as Array<{
      message: Message | StreamingAssistantMessage;
      displayMode: BubbleDisplayMode;
    }>;
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

      <!-- Empty / Loading / Error States -->
      <div v-if="presentedMessages.length === 0" class="flex flex-col items-center justify-center h-64 space-y-4">

        <template v-if="store.historyState.status === 'loading'">
          <Loader2 class="w-12 h-12 text-slate-300 animate-spin" />
          <p class="text-slate-400">Loading messages...</p>
        </template>

        <template v-else-if="store.historyState.status === 'error'">
          <AlertCircle class="w-12 h-12 text-red-400" />
          <p class="text-red-500 font-medium text-center">Failed to load history<br><span class="text-sm font-normal opacity-80">{{ store.historyState.error }}</span></p>
        </template>

        <template v-else>
          <Bot class="w-12 h-12 text-slate-300" />
          <p class="text-slate-400">No messages yet. Say hello!</p>
        </template>

      </div>

      <!-- Message List -->
      <MessageBubble
        v-for="entry in presentedMessages"
        :key="entry.message.metadata.seq_in_session"
        :message="entry.message"
        :display-mode="entry.displayMode"
        :reasoning-visibility-mode="store.visibility.reasoning"
      />
    </div>
  </div>
</template>
