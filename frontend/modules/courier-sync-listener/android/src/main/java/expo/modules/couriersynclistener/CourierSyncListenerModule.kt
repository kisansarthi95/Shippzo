package expo.modules.couriersynclistener

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.records.Field
import expo.modules.kotlin.records.Record

/**
 * JS-facing bridge for the courier-sync NotificationListener.
 *
 * The real work happens in [CourierNotificationListenerService] — this
 * module only persists configuration (backend URL, JWT, sender filter)
 * to SharedPreferences and exposes helpers to navigate the user to the
 * Notification Access settings screen.
 */
class CourierSyncListenerModule : Module() {

  class IngestConfigRecord : Record {
    @Field var backendUrl:    String  = ""
    @Field var authToken:     String  = ""
    @Field var deviceId:      String  = ""
    @Field var senderPattern: String? = "INPOST"
  }

  override fun definition() = ModuleDefinition {
    Name("CourierSyncListener")

    // Phase F8.0 — fired by IngestQueue after each SUCCESSFUL POST to
    // /api/courier-sync/ingest. Open screens (Shipments list, Shipment
    // Details) subscribe and refetch, so the UI refreshes THE MOMENT
    // an SMS lands — no polling.
    Events("onIngestResult")

    OnCreate {
      activeModule = this@CourierSyncListenerModule
    }

    OnDestroy {
      if (activeModule === this@CourierSyncListenerModule) {
        activeModule = null
      }
    }

    Function("isAvailable") {
      // Always true on Android 24+ — we ship the service in this module.
      Build.VERSION.SDK_INT >= Build.VERSION_CODES.N
    }

    Function("isPermissionGranted") {
      isNotificationListenerEnabled(appContext.reactContext)
    }

    Function("openNotificationAccessSettings") {
      val ctx = appContext.reactContext ?: return@Function null
      val intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).apply {
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      }
      try {
        ctx.startActivity(intent)
      } catch (_: Exception) {
        // Fallback — some OEMs hide the listener-specific page; open
        // generic app-notification settings as a degrade-gracefully path.
        val fallback = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
          putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName)
          addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try { ctx.startActivity(fallback) } catch (_: Exception) {}
      }
      // Expo Modules SDK 54 sync `Function` requires the lambda to return
      // `Any?`. Returning `Unit` (the natural last expression here) trips
      // the Kotlin compiler with `Return type mismatch: expected 'Any?',
      // actual 'Unit'`, so we explicitly hand back `null` from every path.
      null
    }

    Function("setIngestConfig") { record: IngestConfigRecord ->
      val ctx = appContext.reactContext ?: return@Function null
      val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      prefs.edit()
        .putString(KEY_BACKEND_URL,    record.backendUrl)
        .putString(KEY_AUTH_TOKEN,     record.authToken)
        .putString(KEY_DEVICE_ID,      record.deviceId)
        .putString(KEY_SENDER_PATTERN, (record.senderPattern ?: "INPOST"))
        .apply()
      null
    }

    Function("setEnabled") { enabled: Boolean ->
      val ctx = appContext.reactContext ?: return@Function null
      ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .edit()
        .putBoolean(KEY_ENABLED, enabled)
        .apply()
      null
    }

    Function("getStatus") {
      val ctx     = appContext.reactContext
      val prefs   = ctx?.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      val urlOk   = !prefs?.getString(KEY_BACKEND_URL, "").isNullOrEmpty()
      val tokenOk = !prefs?.getString(KEY_AUTH_TOKEN,  "").isNullOrEmpty()
      mapOf(
        "available"         to (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N),
        "permissionGranted" to isNotificationListenerEnabled(ctx),
        "ingestConfigured"  to (urlOk && tokenOk),
        "enabled"           to (prefs?.getBoolean(KEY_ENABLED, false) ?: false),
        "pendingQueueCount" to (ctx?.let { IngestQueue.count(it.applicationContext) } ?: 0),
      )
    }

    // Phase F8.0 — drain any SMS payloads that queued up while the
    // device was offline. Called by the React layer on app open /
    // foreground so a fresh JWT is already persisted before retrying.
    Function("flushPendingQueue") {
      val ctx = appContext.reactContext ?: return@Function null
      Thread {
        try {
          IngestQueue.flush(ctx.applicationContext)
        } catch (_: Exception) {
          // never crash the bridge
        }
      }.start()
      null
    }

    Function("getPendingQueueCount") {
      val ctx = appContext.reactContext ?: return@Function 0
      IngestQueue.count(ctx.applicationContext)
    }
  }

  companion object {
    const val PREFS              = "courier_sync_listener_prefs"
    const val KEY_BACKEND_URL    = "backend_url"
    const val KEY_AUTH_TOKEN     = "auth_token"
    const val KEY_DEVICE_ID      = "device_id"
    const val KEY_SENDER_PATTERN = "sender_pattern"
    const val KEY_ENABLED        = "enabled"

    // Phase F8.0 — live module instance for service→JS event delivery.
    // Null whenever the React runtime is not up (app killed); the
    // emit is then a silent no-op and screens refresh on next focus.
    @Volatile
    private var activeModule: CourierSyncListenerModule? = null

    fun emitIngestResult(data: Map<String, Any?>) {
      try {
        activeModule?.sendEvent("onIngestResult", data)
      } catch (_: Exception) {
        // no-op — never let a JS-bridge hiccup break SMS delivery
      }
    }

    fun isNotificationListenerEnabled(ctx: Context?): Boolean {
      if (ctx == null) return false
      val flat = Settings.Secure.getString(
        ctx.contentResolver,
        "enabled_notification_listeners",
      ) ?: return false
      val component = ComponentName(
        ctx,
        CourierNotificationListenerService::class.java,
      ).flattenToString()
      return flat.split(":").any { it.equals(component, ignoreCase = true) }
    }
  }
}
