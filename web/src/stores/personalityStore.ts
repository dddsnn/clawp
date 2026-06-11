// Copyright 2026 Marc Lehmann
//
// This file is part of clawp.
//
// clawp is free software: you can redistribute it and/or modify it under the
// terms of the GNU Affero General Public License as published by the Free
// Software Foundation, either version 3 of the License, or (at your option) any
// later version.
//
// clawp is distributed in the hope that it will be useful, but WITHOUT ANY
// WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
// A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
// details.
//
// You should have received a copy of the GNU Affero General Public License along
// with clawp. If not, see <https://www.gnu.org/licenses/>.

import { defineStore } from 'pinia';
import { ref } from 'vue';
import { fetchPersonalities as fetchPersonalitiesApi, fetchPersonality as fetchPersonalityApi } from '../services/api';
import type { AgentPersonality, AgentPersonalityWithFileContents } from '../types/api';

export const usePersonalityStore = defineStore('personality', () => {
  const personalities = ref<AgentPersonality[]>([]);
  const personalitiesLoading = ref(false);
  const personalitiesError = ref<string | null>(null);

  const selectedPersonalityName = ref<string | null>(null);
  const personalityDetails = ref<AgentPersonalityWithFileContents | null>(null);
  const personalityDetailsLoading = ref(false);
  const personalityDetailsError = ref<string | null>(null);
  const personalityRequestCounter = ref(0);

  async function fetchPersonalities() {
    try {
      personalitiesLoading.value = true;
      personalitiesError.value = null;
      personalities.value = await fetchPersonalitiesApi();
    } catch (error) {
      console.error('Failed to load personalities:', error);
      personalitiesError.value = error instanceof Error ? error.message : String(error);
      personalities.value = [];
    } finally {
      personalitiesLoading.value = false;
    }
  }

  async function fetchPersonality(personalityName: string) {
    const requestId = ++personalityRequestCounter.value;

    personalityDetailsLoading.value = true;
    personalityDetailsError.value = null;
    personalityDetails.value = null;

    try {
      const details = await fetchPersonalityApi(personalityName);
      if (requestId !== personalityRequestCounter.value) {
        return;
      }
      personalityDetails.value = details;
    } catch (error) {
      if (requestId !== personalityRequestCounter.value) {
        return;
      }
      console.error(`Failed to load personality ${personalityName}:`, error);
      personalityDetailsError.value = error instanceof Error ? error.message : String(error);
    } finally {
      if (requestId === personalityRequestCounter.value) {
        personalityDetailsLoading.value = false;
      }
    }
  }

  function setSelectedPersonalityName(name: string | null) {
    selectedPersonalityName.value = name;
  }

  return {
    personalities,
    personalitiesLoading,
    personalitiesError,
    selectedPersonalityName,
    personalityDetails,
    personalityDetailsLoading,
    personalityDetailsError,
    fetchPersonalities,
    fetchPersonality,
    setSelectedPersonalityName,
  };
});
