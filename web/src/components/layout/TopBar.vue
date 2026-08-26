<!--
Copyright 2026 Marc Lehmann

This file is part of clawp.
-->

<script setup lang="ts">
import { computed } from 'vue';
import { storeToRefs } from 'pinia';
import { ChevronRight, Home } from 'lucide-vue-next';
import { useRoute } from 'vue-router';
import { useAgentStore } from '../../stores/agentStore';

type Breadcrumb = {
  label: string;
  detail?: string;
  to?: { name: string; params?: Record<string, string> };
};

const route = useRoute();
const { agents } = storeToRefs(useAgentStore());

const breadcrumbs = computed<Breadcrumb[]>(() => {
  switch (route.name) {
    case 'agents':
      return [{ label: 'Agents' }];
    case 'agent-chat':
    case 'agent-management': {
      const agentId = String(route.params.agentId);
      const agent = agents.value.find((candidate) => candidate.id === agentId);
      return [
        { label: 'Agents', to: { name: 'agents' } },
        { label: agent?.name ?? 'Agent', detail: ` (${agentId})`, to: { name: 'agent', params: { agentId } } },
        { label: route.name === 'agent-chat' ? 'Chat' : 'Management' },
      ];
    }
    case 'personalities':
      return [{ label: 'Personalities' }];
    case 'personality-details':
      return [
        { label: 'Personalities', to: { name: 'personalities' } },
        { label: String(route.params.personalityName) },
      ];
    case 'channels':
      return [{ label: 'Channels' }];
    case 'channel-details':
      return [
        { label: 'Channels', to: { name: 'channels' } },
        { label: String(route.params.channelType), detail: `:${String(route.params.channelId)}` },
      ];
    default:
      return [];
  }
});
</script>

<template>
  <div class="sticky top-0 z-10 flex flex-col">
    <header class="flex h-12 items-center gap-6 border-b bg-white px-4 shadow-sm">
      <h1 class="shrink-0 text-xl font-semibold tracking-tight text-slate-800">Clawp</h1>

      <nav aria-label="Breadcrumb" class="flex min-w-0 self-stretch items-center overflow-x-auto overflow-y-hidden">
        <ol class="flex min-w-max items-center gap-1 text-sm text-slate-500">
          <li>
            <RouterLink v-if="route.name !== 'home'" :to="{ name: 'home' }" class="rounded p-1 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800" title="Home">
              <Home class="h-4 w-4" />
            </RouterLink>
            <span v-else class="block p-1 text-slate-800" aria-current="page" title="Home"><Home class="h-4 w-4" /></span>
          </li>
          <template v-for="(breadcrumb, index) in breadcrumbs" :key="`${breadcrumb.label}:${breadcrumb.detail ?? ''}:${index}`">
            <li aria-hidden="true"><ChevronRight class="h-4 w-4 text-slate-300" /></li>
            <li class="whitespace-nowrap">
              <RouterLink v-if="breadcrumb.to" :to="breadcrumb.to" class="rounded px-1 py-0.5 transition-colors hover:bg-slate-100 hover:text-slate-800">
                {{ breadcrumb.label }}<span v-if="breadcrumb.detail" class="font-mono text-xs text-slate-400">{{ breadcrumb.detail }}</span>
              </RouterLink>
              <span v-else class="px-1 py-0.5 font-medium text-slate-800" :aria-current="index === breadcrumbs.length - 1 ? 'page' : undefined">
                {{ breadcrumb.label }}<span v-if="breadcrumb.detail" class="font-mono text-xs text-slate-500">{{ breadcrumb.detail }}</span>
              </span>
            </li>
          </template>
        </ol>
      </nav>
    </header>
  </div>
</template>
