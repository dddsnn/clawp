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
import { storeToRefs } from 'pinia';
import { useChatStore } from '../../stores/chatStore';
import { WifiOff, Loader2 } from 'lucide-vue-next';

const chatStore = useChatStore();
const { connectionState } = storeToRefs(chatStore);
</script>

<template>
  <div class="flex flex-col z-10 sticky top-0">
    <!-- Connection Status Banner -->
    <div v-if="connectionState.status === 'disconnected'" class="bg-slate-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
      <WifiOff class="w-4 h-4" />
      <span>Disconnected from API.</span>
    </div>
    <div v-else-if="connectionState.status === 'connecting' && connectionState.error" class="bg-red-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
      <Loader2 class="w-4 h-4 animate-spin" />
      <span>Error: {{ connectionState.error }}. Reconnecting... (Attempt {{ connectionState.attempt }})</span>
    </div>
    <div v-else-if="connectionState.status === 'connecting'" class="bg-blue-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
      <Loader2 class="w-4 h-4 animate-spin" />
      <span>Connecting to API... (Attempt {{ connectionState.attempt }})</span>
    </div>

    <!-- Main Header -->
    <header class="flex items-center justify-between px-4 py-3 bg-white border-b shadow-sm">
      <div class="flex items-center space-x-2">
        <h1 class="text-xl font-semibold text-slate-800 tracking-tight">Clawp AI agent framework</h1>
      </div>
    </header>
  </div>
</template>
