# courier-sync-listener

Local Expo module (Android-only) that bridges Android's `NotificationListenerService`
to the Shippzo backend `POST /api/courier-sync/ingest` endpoint.

## What it does

1. Registers a system NotificationListenerService.
2. After the operator grants "Notification access" in System Settings, every
   SMS the device receives is inspected by the service.
3. If the SMS sender / body contains the configured sender pattern (default
   `INPOST` for India Post DLT senders like `VA-INPOST-G`), the raw body is
   POST-ed to the backend.
4. Everything else is dropped on-device — the backend never sees private SMS.

## Why NotificationListener (not READ_SMS)

- `READ_SMS` is a Play-Store-restricted permission. Apps that request it
  routinely get removed unless they have a declared SMS-handler role.
- `NotificationListener` works for ANY messenger app (Google Messages,
  Samsung Messages, MIUI Messaging, OEM forks). The user opts in once via
  System Settings → Notification access.

## Testing

This module CANNOT run inside **Expo Go**. To test it:

1. Click **Publish** in Emergent (top-right) and wait for the deploy.
2. Generate an Android APK via the Emergent build flow.
3. Install the APK on an Android device.
4. Open the app → Settings → **Courier Auto Sync** → enable India Post
   → tap **Grant Notification Access** → toggle Shippzo in the OS list.
5. Receive an India Post SMS — the shipment auto-updates within seconds.

During development you can still exercise the **Test Parse** and **Live
Ingest** buttons on the Courier Auto Sync screen — those use the same
backend endpoints the native service POSTs to.
