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
import { AlertCircle, FileText, Loader2 } from 'lucide-vue-next';
import type { AgentPersonalityWithFileContents } from '../../types/api';

defineProps<{
  selectedPersonalityName: string | null;
  isLoading: boolean;
  error: string | null;
  personality: AgentPersonalityWithFileContents | null;
}>();
</script>

<template>
  <div class="flex-1 overflow-y-auto bg-slate-50 p-6">
    <div v-if="!selectedPersonalityName" class="h-full flex items-center justify-center text-slate-400">
      Select a personality to view details.
    </div>

    <div v-else-if="isLoading" class="h-full flex flex-col items-center justify-center space-y-3 text-slate-500">
      <Loader2 class="w-8 h-8 animate-spin" />
      <span>Loading personality details...</span>
    </div>

    <div v-else-if="error" class="h-full flex flex-col items-center justify-center space-y-3 text-red-500">
      <AlertCircle class="w-8 h-8" />
      <p>Failed to load personality details.</p>
      <p class="text-sm opacity-80">{{ error }}</p>
    </div>

    <div v-else-if="personality" class="max-w-5xl mx-auto space-y-6">
      <header class="space-y-1">
        <h2 class="text-2xl font-semibold text-slate-800">{{ personality.name }}</h2>
        <p class="text-sm text-slate-500">Personality files</p>
      </header>

      <section class="space-y-4">
        <article
          v-for="personalityFile in personality.personality_files"
          :key="personalityFile.path"
          class="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden"
        >
          <div class="px-4 py-3 border-b border-slate-100 bg-slate-50">
            <div class="flex items-center space-x-2 text-slate-800">
              <FileText class="w-4 h-4" />
              <h3 class="font-mono text-sm">{{ personalityFile.path }}</h3>
            </div>
            <p class="mt-1 text-sm text-slate-600">{{ personalityFile.description }}</p>
          </div>

          <div class="p-4">
            <pre
              v-if="personality.personality_file_contents[personalityFile.path] != null"
              class="text-xs text-slate-700 whitespace-pre-wrap break-words font-mono"
            >{{ personality.personality_file_contents[personalityFile.path] }}</pre>
            <div v-else class="text-sm italic text-slate-500">this file doesn't exist</div>
          </div>
        </article>
      </section>
    </div>
  </div>
</template>
