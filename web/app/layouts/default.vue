<template>
  <div class="min-h-screen">
    <div class="aurora-bg pointer-events-none fixed inset-0 -z-10" aria-hidden="true" />
    <WallpaperBackdrop />
    <AppTopBar />
    <SectionBar />
    <AnnouncementBanner />
    <div class="mx-auto max-w-[1280px] px-4 lg:px-6">
      <div
        class="grid gap-6 py-6 lg:grid-cols-[220px_minmax(0,1fr)]"
        :class="{ 'xl:grid-cols-[220px_minmax(0,1fr)_300px]': !contentFocus }"
      >
        <aside class="hidden lg:block">
          <div class="sticky top-[6.5rem]">
            <AppSidebar />
          </div>
        </aside>
        <main class="min-w-0">
          <slot />
        </main>
        <aside v-if="!contentFocus" class="hidden xl:block">
          <div class="sticky top-[6.5rem] space-y-4">
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
const contentFocus = computed(() => route.path === '/stats' || /^\/contents\/[^/]+$/.test(route.path))
</script>
