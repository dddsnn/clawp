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
import { AlertCircle, Link } from 'lucide-vue-next';
import type { ChannelInformation } from '../../types/api';

defineProps<{
  channel: ChannelInformation | null;
}>();
</script>

<template>
  <div class="flex-1 overflow-y-auto bg-slate-50 p-6">
    <div v-if="!channel" class="h-full flex flex-col items-center justify-center space-y-2 text-slate-400">
      <AlertCircle class="w-7 h-7" />
      <span>Selected channel not found.</span>
    </div>

    <div v-else class="max-w-4xl mx-auto space-y-6">
      <header class="space-y-1">
        <h2 class="text-2xl font-semibold text-slate-800">{{ channel.type }}:{{ channel.id ?? '(none)' }}</h2>
        <p class="text-sm text-slate-500">Configured channel details</p>
      </header>

      <section class="bg-white border border-slate-200 rounded-xl shadow-sm p-4 space-y-3">
        <h3 class="text-sm font-semibold text-slate-700 uppercase tracking-wide">Overview</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <div class="rounded-lg bg-slate-50 border border-slate-200 p-3">
            <p class="text-slate-500">Type</p>
            <p class="font-medium text-slate-800">{{ channel.type }}</p>
          </div>
          <div class="rounded-lg bg-slate-50 border border-slate-200 p-3">
            <p class="text-slate-500">Channel ID</p>
            <p class="font-mono text-slate-800">{{ channel.id ?? '(none)' }}</p>
          </div>
          <div class="rounded-lg bg-slate-50 border border-slate-200 p-3 md:col-span-2">
            <p class="text-slate-500">Assigned to Agent</p>
            <p class="font-mono text-slate-800">{{ channel.assigned_to_agent ?? 'unassigned' }}</p>
          </div>
        </div>
      </section>

      <section class="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
        <div class="px-4 py-3 border-b border-slate-100 bg-slate-50 flex items-center space-x-2">
          <Link class="w-4 h-4 text-slate-600" />
          <h3 class="text-sm font-semibold text-slate-700 uppercase tracking-wide">Config</h3>
        </div>
        <pre class="p-4 text-xs text-slate-700 whitespace-pre-wrap break-words font-mono">{{ JSON.stringify(channel.config, null, 2) }}</pre>
      </section>

      <section class="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
        <div class="px-4 py-3 border-b border-slate-100 bg-slate-50 flex items-center space-x-2">
          <Link class="w-4 h-4 text-slate-600" />
          <h3 class="text-sm font-semibold text-slate-700 uppercase tracking-wide">Status</h3>
        </div>
        <pre class="p-4 text-xs text-slate-700 whitespace-pre-wrap break-words font-mono">{{ JSON.stringify(channel.status, null, 2) }}</pre>
      </section>
    </div>
  </div>
</template>
