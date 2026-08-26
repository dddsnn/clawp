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
import { AlertCircle, Bot, BotOff, ChevronLeft, Loader2, Plus, Radio, User } from 'lucide-vue-next';
import type { AgentInformation, AgentPersonality, ChannelInformation } from '../../types/api';
import { getChannelKey } from '../../stores/channelStore';

type Collection = 'root' | 'agents' | 'personalities' | 'channels';

defineProps<{
  collection: Collection;
  agents: AgentInformation[];
  selectedAgentId: string | null;
  agentsLoading: boolean;
  agentsError: string | null;
  personalities: AgentPersonality[];
  selectedPersonalityName: string | null;
  personalitiesLoading: boolean;
  personalitiesError: string | null;
  channels: ChannelInformation[];
  selectedChannelKey: string | null;
  channelsLoading: boolean;
  channelsError: string | null;
  canHatch: boolean;
  isHatching: boolean;
}>();

const emit = defineEmits<{
  navigateCollection: [collection: Exclude<Collection, 'root'>];
  navigateBack: [];
  selectAgent: [id: string];
  selectPersonality: [name: string];
  selectChannel: [channel: ChannelInformation];
  openHatchModal: [];
}>();
</script>

<template>
  <aside class="w-64 shrink-0 z-10 flex flex-col border-r border-slate-200 bg-white">
    <div v-if="collection === 'root'" class="flex-1 space-y-1 overflow-y-auto p-2">
      <button
        class="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
        @click="emit('navigateCollection', 'agents')"
      >
        <Bot class="h-5 w-5" />
        <span class="text-sm font-medium">Agents</span>
      </button>
      <button
        class="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
        @click="emit('navigateCollection', 'personalities')"
      >
        <User class="h-5 w-5" />
        <span class="text-sm font-medium">Personalities</span>
      </button>
      <button
        class="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
        @click="emit('navigateCollection', 'channels')"
      >
        <Radio class="h-5 w-5" />
        <span class="text-sm font-medium">Channels</span>
      </button>
    </div>

    <div v-else class="flex-1 overflow-y-auto p-2">
      <div class="mb-3 flex items-center gap-2 px-2 py-2">
        <button
          class="rounded-md p-1 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
          title="Back to home"
          @click="emit('navigateBack')"
        >
          <ChevronLeft class="h-5 w-5" />
        </button>
        <div class="flex items-center gap-2">
          <Bot v-if="collection === 'agents'" class="h-4 w-4 text-slate-500" />
          <User v-else-if="collection === 'personalities'" class="h-4 w-4 text-slate-500" />
          <Radio v-else class="h-4 w-4 text-slate-500" />
          <h2 class="text-xs font-semibold uppercase tracking-wide text-slate-700">{{ collection }}</h2>
        </div>
        <button
          v-if="collection === 'agents'"
          class="ml-auto inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!canHatch || isHatching"
          :title="canHatch ? 'Create a new agent from a personality' : 'Hatching unavailable until personalities are loaded'"
          @click="emit('openHatchModal')"
        >
          <Loader2 v-if="isHatching" class="h-3.5 w-3.5 animate-spin" />
          <Plus v-else class="h-3.5 w-3.5" />
          Hatch
        </button>
      </div>

      <div v-if="collection === 'agents'" class="space-y-1">
        <div v-if="agentsLoading" class="flex flex-col items-center justify-center space-y-2 p-4 text-xs text-slate-400"><Loader2 class="h-6 w-6 animate-spin" /><span>Loading agents...</span></div>
        <div v-else-if="agentsError" class="flex flex-col items-center justify-center space-y-2 p-4 text-center text-xs text-red-500"><AlertCircle class="h-6 w-6" /><span>Failed to load agents<br>{{ agentsError }}</span></div>
        <div v-else-if="agents.length === 0" class="p-4 text-center text-sm text-slate-400">No agents available.</div>
        <button v-for="agent in agents" v-else :key="agent.id" class="w-full rounded-lg px-3 py-2.5 text-left transition-colors" :class="selectedAgentId === agent.id ? 'bg-blue-50 font-medium text-blue-700 shadow-sm ring-1 ring-blue-500/20' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'" :title="`${agent.name} (${agent.id})`" @click="emit('selectAgent', agent.id)">
          <span class="block truncate text-sm font-medium">{{ agent.name }}</span>
          <span class="block truncate font-mono text-xs opacity-70">{{ agent.id }}</span>
        </button>
      </div>

      <div v-else-if="collection === 'personalities'" class="space-y-1">
        <div v-if="personalitiesLoading" class="flex flex-col items-center justify-center space-y-2 p-4 text-xs text-slate-400"><Loader2 class="h-6 w-6 animate-spin" /><span>Loading personalities...</span></div>
        <div v-else-if="personalitiesError" class="flex flex-col items-center justify-center space-y-2 p-4 text-center text-xs text-red-500"><AlertCircle class="h-6 w-6" /><span>Failed to load personalities<br>{{ personalitiesError }}</span></div>
        <div v-else-if="personalities.length === 0" class="p-4 text-center text-sm text-slate-400">No personalities available.</div>
        <button v-for="personality in personalities" v-else :key="personality.name" class="w-full truncate rounded-lg px-3 py-2.5 text-left text-sm transition-colors" :class="selectedPersonalityName === personality.name ? 'bg-violet-50 font-medium text-violet-700 shadow-sm ring-1 ring-violet-500/20' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'" :title="personality.name" @click="emit('selectPersonality', personality.name)">
          {{ personality.name }}
        </button>
      </div>

      <div v-else class="space-y-1">
        <div v-if="channelsLoading" class="flex flex-col items-center justify-center space-y-2 p-4 text-xs text-slate-400"><Loader2 class="h-6 w-6 animate-spin" /><span>Loading channels...</span></div>
        <div v-else-if="channelsError" class="flex flex-col items-center justify-center space-y-2 p-4 text-center text-xs text-red-500"><AlertCircle class="h-6 w-6" /><span>Failed to load channels<br>{{ channelsError }}</span></div>
        <div v-else-if="channels.length === 0" class="p-4 text-center text-sm text-slate-400">No channels configured.</div>
        <div v-for="channel in channels" v-else :key="getChannelKey(channel)" class="flex items-center gap-1">
          <button class="flex-1 truncate rounded-lg px-3 py-2.5 text-left text-sm transition-colors disabled:cursor-not-allowed" :class="[!channel.status.available ? 'text-slate-400 hover:bg-slate-100' : selectedChannelKey === getChannelKey(channel) ? 'bg-emerald-50 font-medium text-emerald-700 shadow-sm ring-1 ring-emerald-500/20' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900']" :disabled="channel.id === null" :title="`${channel.type}:${channel.id ?? '(none)'}${channel.status.available ? '' : ' (unavailable)'}`" @click="emit('selectChannel', channel)">
            <span class="font-medium">{{ channel.type }}</span><span class="text-slate-400">:</span><span class="font-mono">{{ channel.id ?? '(none)' }}</span>
          </button>
          <Bot v-if="channel.assigned_to_agent" :class="['h-4 w-4 shrink-0', channel.status.available ? 'text-slate-500' : 'text-slate-300']" />
          <BotOff v-else :class="['h-4 w-4 shrink-0', channel.status.available ? 'text-slate-500' : 'text-slate-300']" />
        </div>
      </div>
    </div>
  </aside>
</template>
