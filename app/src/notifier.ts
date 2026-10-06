// Desktop notifications (non-intrusive, OS-native). Failures to notify are never fatal.
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";

let allowed: boolean | null = null;

export async function desktopNotify(title: string, body: string): Promise<void> {
  try {
    if (allowed === null) {
      allowed = (await isPermissionGranted()) || (await requestPermission()) === "granted";
    }
    if (allowed) sendNotification({ title, body });
  } catch {
    allowed = false; // the OS may have no notification service; the edge still shows the message
  }
}
