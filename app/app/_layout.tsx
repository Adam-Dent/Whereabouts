import { Stack } from "expo-router";

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title: "Hereabouts" }} />
      <Stack.Screen name="detail/[id]" options={{ title: "House" }} />
    </Stack>
  );
}
