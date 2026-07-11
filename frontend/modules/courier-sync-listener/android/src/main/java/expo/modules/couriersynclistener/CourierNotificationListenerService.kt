package expo.modules.couriersynclistener

import android.app.Notification
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * NotificationListenerService that filters incoming SMS-app notifications
 * for India Post DLT senders (or whatever sender pattern is configured)
 * and POSTs the raw body to the backend ingest endpoint.
 *
 * Reasons for choosing a NotificationListener over READ_SMS:
 *   • READ_SMS is a Play-Store-restricted permission; using it requires
 *     a manual policy declaration and routinely gets apps removed.
 *   • NotificationListener works for ANY messaging app that posts SMS
 *     notifications — Google Messages, Samsung Messages, MIUI Messaging
 *     and the OEM Messenger forks all post identical Notification objects.
 *   • The user explicitly opts in via System Settings → Notification access,
 *     which is the standard Android UX for this kind of background reader.
 *
 * The service is intentionally LIGHT — we keep zero state in memory, read
 * the latest filter / backend / token from SharedPreferences on every
 * notification (so changes from the React layer take effect instantly).
 *
 * Phase F4.8 — Verbose Log.d instrumentation at every gate so operators
 * can `adb logcat -s CourierSync` and see exactly why a given SMS was
 * or wasn't ingested. Every log line starts with a per-notification
 * short id (last 6 chars of the SBN key) so logs from concurrent
 * notifications are easy to correlate.
 */
class CourierNotificationListenerService : NotificationListenerService() {

  private val executor = Executors.newSingleThreadExecutor()
  private var networkCallback: ConnectivityManager.NetworkCallback? = null

  // ── Phase F8.0 — Offline recovery hooks ─────────────────────────
  // 1. When connectivity returns (mobile data toggled back ON), drain
  //    every SMS payload that queued up while the device was offline.
  // 2. When the OS (re)binds the listener, drain too — covers reboots
  //    and app updates where queued entries may be waiting.
  override fun onCreate() {
    super.onCreate()
    try {
      val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
      val cb = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
          Log.d(TAG, "network=available — flushing pending ingest queue")
          executor.execute { IngestQueue.flush(applicationContext) }
        }
      }
      cm.registerDefaultNetworkCallback(cb)
      networkCallback = cb
      Log.d(TAG, "network callback registered")
    } catch (e: Exception) {
      Log.w(TAG, "network callback registration failed: " + (e.message ?: ""))
    }
  }

  override fun onDestroy() {
    try {
      val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
      networkCallback?.let { cm.unregisterNetworkCallback(it) }
    } catch (_: Exception) {
      // no-op
    }
    networkCallback = null
    super.onDestroy()
  }

  override fun onListenerConnected() {
    super.onListenerConnected()
    Log.d(TAG, "listener=connected — flushing pending ingest queue")
    executor.execute { IngestQueue.flush(applicationContext) }
  }

  override fun onNotificationPosted(sbn: StatusBarNotification?) {
    if (sbn == null) {
      Log.d(TAG, "onNotificationPosted: sbn=null — skip")
      return
    }
    val sid = try {
      sbn.key.takeLast(6)
    } catch (_: Exception) {
      "??????"
    }
    val extras = sbn.notification?.extras
    if (extras == null) {
      Log.d(TAG, "[$sid] extras=null pkg=${sbn.packageName.orEmpty()} — skip")
      return
    }

    val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
    val text  = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
      ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString().orEmpty()
    val pkg   = sbn.packageName.orEmpty()

    // ─── LOG raw notification fields — ALWAYS logged so we can see
    // even the ones we drop. Truncate text to avoid multi-MB logs
    // from apps that stuff huge bodies into notifications.
    val textPreview = if (text.length > 200) text.take(200) + "…[+${text.length - 200}]" else text
    Log.d(TAG, "[$sid] recv pkg=$pkg title=\"$title\" text=\"$textPreview\"")

    if (text.isBlank() && title.isBlank()) {
      Log.d(TAG, "[$sid] gate=payload_empty reason=title+text both blank — skip")
      return
    }

    val prefs = applicationContext.getSharedPreferences(
      CourierSyncListenerModule.PREFS,
      Context.MODE_PRIVATE,
    )
    val enabled       = prefs.getBoolean(CourierSyncListenerModule.KEY_ENABLED, false)
    Log.d(TAG, "[$sid] gate=enabled value=$enabled")
    if (!enabled) return

    val senderFilter  = prefs.getString(
      CourierSyncListenerModule.KEY_SENDER_PATTERN, "IndiaPost",
    ).orEmpty()
    val backendUrl    = prefs.getString(CourierSyncListenerModule.KEY_BACKEND_URL, "").orEmpty()
    val authToken     = prefs.getString(CourierSyncListenerModule.KEY_AUTH_TOKEN, "").orEmpty()
    val deviceId      = prefs.getString(CourierSyncListenerModule.KEY_DEVICE_ID, "").orEmpty()

    val configOk = backendUrl.isNotEmpty() && authToken.isNotEmpty()
    // Phase F5.9 — Kotlin's string-template parser trips on nested
    // double-quoted string literals inside `${...}` expressions when
    // those `${...}` themselves live inside another `"..."`. Extract
    // the ternary branches into plain locals FIRST, then just
    // interpolate the locals into a single flat template so the
    // parser only ever sees one level of `"..."`. This is the exact
    // pattern EAS-side Kotlin 2.x rejected as "Expecting an
    // expression" / "'if' must have both branches" earlier.
    val backendUrlDbg = if (backendUrl.isEmpty()) "MISSING" else "set(" + backendUrl.length + "c)"
    val authTokenDbg  = if (authToken.isEmpty())  "MISSING" else "set(" + authToken.length  + "c)"
    val deviceIdDbg   = if (deviceId.isEmpty())   "MISSING" else deviceId
    Log.d(
      TAG,
      "[" + sid + "] gate=config_present backendUrl=" + backendUrlDbg +
        " authToken=" + authTokenDbg +
        " deviceId=" + deviceIdDbg +
        " senderFilter=" + senderFilter,
    )
    if (!configOk) {
      Log.d(TAG, "[$sid] gate=config_present result=FAIL — skip")
      return
    }

    if (senderFilter.isNotEmpty()) {
      // Phase F8.0 — Multi-courier filter. The React layer joins every
      // enabled courier's sender-pattern tokens with "|" (e.g.
      // "INPOST|IPOSTV|IndiaPost|NANDAN"). A notification passes when
      // ANY token appears in the title+text (case-insensitive) — the
      // backend per-courier Scanning Rules remain the real gate.
      val haystack = "$title $text".uppercase()
      val needles = senderFilter
        .split("|")
        .map { it.trim().uppercase() }
        .filter { it.isNotEmpty() }
      val match = needles.isEmpty() || needles.any { haystack.contains(it) }
      Log.d(TAG, "[" + sid + "] gate=sender_filter needles=" + needles.size + " match=" + match)
      if (!match) return
    } else {
      Log.d(TAG, "[$sid] gate=sender_filter needle=EMPTY — pass-through")
    }

    // Only ingest from likely SMS apps to avoid forwarding random
    // app notifications. Wide allow-list — errs on the side of false-
    // positives because the backend parser is the real gate.
    val isSmsApp = pkg.contains("messag", ignoreCase = true) ||
                   pkg.contains("sms",    ignoreCase = true) ||
                   pkg == "com.android.mms" ||
                   pkg == "com.samsung.android.messaging"
    Log.d(TAG, "[$sid] gate=sms_app_match pkg=$pkg match=$isSmsApp")
    if (!isSmsApp) return

    val postedAt = try { sbn.postTime } catch (_: Exception) { System.currentTimeMillis() }

    executor.execute {
      // Phase F8.0 — delivery is routed through IngestQueue so an
      // offline device (mobile data OFF) never loses an SMS: RETRY
      // outcomes are persisted and re-sent when connectivity returns
      // (see onCreate network callback + onListenerConnected).
      val payload = JSONObject().apply {
        put("sender",    title)            // SMS app puts the sender in the title
        put("title",     title)
        put("text",      text)
        put("package",   pkg)
        put("posted_at", java.time.Instant.ofEpochMilli(postedAt).toString())
        put("device_id", deviceId)
      }
      Log.d(TAG, "[" + sid + "] http=POST_pre textLen=" + text.length)
      when (IngestQueue.post(applicationContext, payload)) {
        IngestQueue.OUTCOME_OK -> {
          Log.d(TAG, "[" + sid + "] http=POST_ok — piggyback flush of queued entries")
          IngestQueue.flush(applicationContext)
        }
        IngestQueue.OUTCOME_RETRY -> {
          Log.w(TAG, "[" + sid + "] http=POST_retry — queued for later delivery")
          IngestQueue.enqueue(applicationContext, payload)
        }
        else -> {
          Log.w(TAG, "[" + sid + "] http=POST_dropped (permanent rejection)")
        }
      }
    }
  }

  override fun onNotificationRemoved(sbn: StatusBarNotification?) {
    // No-op — we only care about posting events.
  }

  companion object {
    // Phase F4.8 — Tag intentionally matches the "CourierSync" tag
    // used in the user's `adb logcat -s CourierSync` command so both
    // the React-native side (`Api.courierSync*` axios logs — client-
    // side console) and the native side surface under a single tag.
    private const val TAG = "CourierSync"
  }
}
