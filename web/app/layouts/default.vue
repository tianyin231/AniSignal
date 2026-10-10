<template>
  <div class="min-h-screen">
    <div class="aurora-bg pointer-events-none fixed inset-0 -z-10" aria-hidden="true" />
    <WallpaperBackdrop />
    <AppTopBar />
    <AnnouncementBanner />
    <div class="mx-auto max-w-[1280px] px-4 lg:px-6">
      <div
        class="grid gap-6 py-6"
        :class="{ 'lg:grid-cols-[220px_minmax(0,1fr)]': !newsFocus, 'xl:grid-cols-[220px_minmax(0,1fr)_300px]': !contentFocus }"
      >
        <aside v-if="!newsFocus" class="hidden lg:block">
          <div class="sticky top-[72px]">
            <AppSidebar />
          </div>
        </aside>
        <main class="min-w-0">
          <slot />
        </main>
        <aside v-if="!contentFocus" class="hidden xl:block">
          <div class="sticky top-[72px] space-y-4">
            <RightRail />
          </div>
        </aside>
      </div>
    </div>
    <AppFooter />
  </div>
</template>

<script setup lang="ts">
const route = useRoute()
const newsFocus = computed(() => route.path === '/ai')
const contentFocus = computed(() => newsFocus.value || ['/stats', '/calendar'].includes(route.path) || /^\/contents\/[^/]+$/.test(route.path))
</script>
