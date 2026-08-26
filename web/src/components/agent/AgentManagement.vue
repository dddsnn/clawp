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
import { Loader2 } from 'lucide-vue-next';
import { useAgentStore } from '../../stores/agentStore';

const props = defineProps<{ agentId: string }>();

const agentStore = useAgentStore();
const compactionState = computed(() => agentStore.sessionCompactionStates[props.agentId] ?? null);
const isCompacting = computed(() => compactionState.value?.status === 'compacting');

const compactSession = async () => {
  if (isCompacting.value) {
    return;
  }

  await agentStore.compactSession(props.agentId);
};
</script>

<template>
  <main class="flex-1 overflow-y-auto bg-slate-50 p-4 md:p-8">
    <div class="mx-auto max-w-4xl">
      <section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 class="text-lg font-semibold text-slate-800">Session management</h2>
        <div class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            class="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
            :disabled="isCompacting"
            @click="compactSession"
          >
            <Loader2 v-if="isCompacting" class="h-4 w-4 animate-spin" />
            {{ isCompacting ? 'Compacting…' : 'Compact session' }}
          </button>
          <p class="max-w-xl text-sm text-slate-500">
            Summarize the agent's current session and start a new one to reduce the size of the context window.
          </p>
        </div>
        <p v-if="compactionState?.status === 'completed'" class="mt-3 text-sm text-emerald-700">Session compaction completed.</p>
        <p v-else-if="compactionState?.status === 'error'" class="mt-3 text-sm text-red-600">Failed to compact session: {{ compactionState.error }}</p>
      </section>
    </div>
  </main>
</template>
