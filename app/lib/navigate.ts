/**
 * Platform deep-link builder for native maps (spec §5.7 Navigate).
 */

import { Platform, Linking } from "react-native";

export async function openInMaps(
  lat: number,
  lng: number,
  label: string
): Promise<void> {
  const encoded = encodeURIComponent(label);

  const appleUrl = `https://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`;
  const androidUrl = `geo:${lat},${lng}?q=${lat},${lng}(${encoded})`;
  const googleUrl = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;

  if (Platform.OS === "ios") {
    const canApple = await Linking.canOpenURL(appleUrl);
    await Linking.openURL(canApple ? appleUrl : googleUrl);
  } else if (Platform.OS === "android") {
    const canGeo = await Linking.canOpenURL(androidUrl);
    await Linking.openURL(canGeo ? androidUrl : googleUrl);
  } else {
    await Linking.openURL(googleUrl);
  }
}
