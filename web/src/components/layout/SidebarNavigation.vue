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
import { AlertCircle, Bot, BotOff, Loader2, Plus, User, Radio } from 'lucide-vue-next';
import type { AgentState, AgentPersonality, ChannelInformation } from '../../types/api';
import { getChannelKey } from '../../stores/channelStore';

defineProps<{
  agents: AgentState[];
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
  activeSelectionType: 'agent' | 'personality' | 'channel' | null;
  canHatch: boolean;
  isHatching: boolean;
}>();

const emit = defineEmits<{
  selectAgent: [id: string];
  selectPersonality: [name: string];
  selectChannel: [key: string];
  openHatchModal: [];
}>();

const handleSelectAgent = (agentId: string) => {
  emit('selectAgent', agentId);
};

const handleSelectPersonality = (personalityName: string) => {
  emit('selectPersonality', personalityName);
};

const handleSelectChannel = (channel: ChannelInformation) => {
  emit('selectChannel', getChannelKey(channel));
};

const handleOpenHatchModal = () => {
  emit('openHatchModal');
};
</script>

<template>
  <aside class="w-64 bg-white border-r border-slate-200 flex flex-col flex-shrink-0 z-10">
    <div class="flex-1 overflow-y-auto p-2 space-y-5">
      <section>
        <div class="px-2 py-2 flex items-center justify-between gap-2">
          <div class="flex items-center space-x-2">
            <Bot class="w-4 h-4 text-slate-500" />
            <h2 class="text-xs font-semibold text-slate-700 tracking-wide uppercase">Agents</h2>
          </div>

          <button
            class="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!canHatch || isHatching"
            :title="canHatch ? 'Create a new agent from a personality' : 'Hatching unavailable until personalities are loaded'"
            @click="handleOpenHatchModal"
          >
            <Loader2 v-if="isHatching" class="w-3.5 h-3.5 animate-spin" />
            <Plus v-else class="w-3.5 h-3.5" />
            Hatch
          </button>
        </div>

        <div class="space-y-1">
          <div v-if="agentsLoading" class="flex flex-col items-center justify-center p-4 space-y-2 text-slate-400">
            <Loader2 class="w-6 h-6 animate-spin" />
            <span class="text-xs">Loading agents...</span>
          </div>

          <div v-else-if="agentsError" class="flex flex-col items-center justify-center p-4 space-y-2 text-red-400">
            <AlertCircle class="w-6 h-6 text-red-400" />
            <span class="text-xs text-center text-red-500">Failed to load agents<br><span class="opacity-80">{{ agentsError }}</span></span>
          </div>

          <div v-else-if="agents.length === 0" class="text-sm text-slate-400 p-4 text-center">
            No agents available.
          </div>

          <template v-else>
            <button
              v-for="agent in agents"
              :key="agent.id"
              @click="handleSelectAgent(agent.id)"
              class="w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all duration-200 truncate font-mono"
              :class="[
                activeSelectionType === 'agent' && selectedAgentId === agent.id
                  ? 'bg-blue-50 text-blue-700 font-medium shadow-sm ring-1 ring-blue-500/20'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
              ]"
              :title="agent.id"
            >
              {{ agent.id }}
            </button>
          </template>
        </div>
      </section>

      <section class="pt-4 border-t border-slate-100">
        <div class="px-2 py-2 flex items-center space-x-2">
          <User class="w-4 h-4 text-slate-500" />
          <h2 class="text-xs font-semibold text-slate-700 tracking-wide uppercase">Personalities</h2>
        </div>

        <div class="space-y-1">
          <div v-if="personalitiesLoading" class="flex flex-col items-center justify-center p-4 space-y-2 text-slate-400">
            <Loader2 class="w-6 h-6 animate-spin" />
            <span class="text-xs">Loading personalities...</span>
          </div>

          <div v-else-if="personalitiesError" class="flex flex-col items-center justify-center p-4 space-y-2 text-red-400">
            <AlertCircle class="w-6 h-6 text-red-400" />
            <span class="text-xs text-center text-red-500">Failed to load personalities<br><span class="opacity-80">{{ personalitiesError }}</span></span>
          </div>

          <div v-else-if="personalities.length === 0" class="text-sm text-slate-400 p-4 text-center">
            No personalities available.
          </div>

          <template v-else>
            <button
              v-for="personality in personalities"
              :key="personality.name"
              @click="handleSelectPersonality(personality.name)"
              class="w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all duration-200 truncate"
              :class="[
                activeSelectionType === 'personality' && selectedPersonalityName === personality.name
                  ? 'bg-violet-50 text-violet-700 font-medium shadow-sm ring-1 ring-violet-500/20'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
              ]"
              :title="personality.name"
            >
              {{ personality.name }}
            </button>
          </template>
        </div>
      </section>

      <section class="pt-4 border-t border-slate-100">
        <div class="px-2 py-2 flex items-center space-x-2">
          <Radio class="w-4 h-4 text-slate-500" />
          <h2 class="text-xs font-semibold text-slate-700 tracking-wide uppercase">Channels</h2>
        </div>

        <div class="space-y-1">
          <div v-if="channelsLoading" class="flex flex-col items-center justify-center p-4 space-y-2 text-slate-400">
            <Loader2 class="w-6 h-6 animate-spin" />
            <span class="text-xs">Loading channels...</span>
          </div>

          <div v-else-if="channelsError" class="flex flex-col items-center justify-center p-4 space-y-2 text-red-400">
            <AlertCircle class="w-6 h-6 text-red-400" />
            <span class="text-xs text-center text-red-500">Failed to load channels<br><span class="opacity-80">{{ channelsError }}</span></span>
          </div>

          <div v-else-if="channels.length === 0" class="text-sm text-slate-400 p-4 text-center">
            No channels configured.
          </div>

          <template v-else>
            <div
              v-for="channel in channels"
              :key="getChannelKey(channel)"
              class="flex items-center gap-1"
            >
              <button
                @click="handleSelectChannel(channel)"
                class="flex-1 text-left px-3 py-2.5 rounded-lg text-sm transition-all duration-200 truncate"
                :class="[
                  !channel.status.available
                    ? 'text-slate-400 hover:bg-slate-100 hover:text-slate-600'
                    : activeSelectionType === 'channel' && selectedChannelKey === getChannelKey(channel)
                    ? 'bg-emerald-50 text-emerald-700 font-medium shadow-sm ring-1 ring-emerald-500/20'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                ]"
                :title="`${channel.type}:${channel.id ?? '(none)'}${channel.status.available ? '' : ' (unavailable)'}`"
              >
                {{ channel.type }}:{{ channel.id ?? '(none)' }}
              </button>

              <Bot
                v-if="channel.assigned_to_agent"
                :class="['w-4 h-4 shrink-0', channel.status.available ? 'text-slate-500' : 'text-slate-300']"
              >
                <title>This channel is assigned to {{ channel.assigned_to_agent }}</title>
              </Bot>
              <BotOff
                v-else
                :class="['w-4 h-4 shrink-0', channel.status.available ? 'text-slate-500' : 'text-slate-300']"
              >
                <title>This channel is not assigned to an agent</title>
              </BotOff>
            </div>
          </template>
        </div>
      </section>
    </div>
  </aside>
</template>
