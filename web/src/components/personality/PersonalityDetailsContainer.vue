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
import { watch } from 'vue';
import PersonalityDetails from './PersonalityDetails.vue';
import { usePersonalityStore } from '../../stores/personalityStore';

const props = defineProps<{
  personalityName: string;
}>();

const personalityStore = usePersonalityStore();
const {
  personalityDetails,
  personalityDetailsLoading,
  personalityDetailsError,
} = storeToRefs(personalityStore);

watch(
  () => props.personalityName,
  async (personalityName) => {
    await personalityStore.fetchPersonality(personalityName);
  },
  { immediate: true },
);
</script>

<template>
  <PersonalityDetails
    :selected-personality-name="personalityName"
    :is-loading="personalityDetailsLoading"
    :error="personalityDetailsError"
    :personality="personalityDetails"
  />
</template>
