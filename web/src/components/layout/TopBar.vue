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
import { Eye, EyeOff, WifiOff, Loader2 } from 'lucide-vue-next';

const chatStore = useChatStore();
const { visibility, connectionStatus } = storeToRefs(chatStore);
</script>

<template>
  <div class="flex flex-col z-10 sticky top-0">
    <!-- Connection Status Banner -->
    <div v-if="connectionStatus === 'error' || connectionStatus === 'disconnected'" class="bg-red-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
      <WifiOff class="w-4 h-4" />
      <span>Disconnected from API. Reconnecting...</span>
    </div>
    <div v-else-if="connectionStatus === 'connecting'" class="bg-blue-500 text-white px-4 py-1.5 text-sm flex items-center justify-center space-x-2 shadow-inner">
      <Loader2 class="w-4 h-4 animate-spin" />
      <span>Connecting to API...</span>
    </div>

    <!-- Main Header -->
    <header class="flex items-center justify-between px-4 py-3 bg-white border-b shadow-sm">
      <div class="flex items-center space-x-2">
        <h1 class="text-xl font-semibold text-slate-800 tracking-tight">Clawp AI assistant framework</h1>
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
  </div>
</template>
