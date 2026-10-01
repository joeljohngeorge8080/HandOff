package com.gesturesync

import android.app.*
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.IBinder
import android.util.Log
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarkerResult
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.framework.image.BitmapImageBuilder
import okhttp3.*
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class GestureDropService : LifecycleService() {

    companion object {
        private const val TAG = "GestureDrop"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "gesture_sync_channel"

        const val EXTRA_SERVER_URL  = "server_url"
        const val EXTRA_SHOW_CAMERA = "show_camera"
        const val ACTION_CONTENT_RECEIVED = "com.gesturesync.CONTENT_RECEIVED"
        const val ACTION_STATUS_UPDATE    = "com.gesturesync.STATUS_UPDATE"
    }

    // Camera & ML
    private lateinit var cameraExecutor: ExecutorService
    private var handLandmarker: HandLandmarker? = null

    // Network
    private var webSocket: WebSocket? = null
    private val okHttpClient = OkHttpClient()

    // Gesture state
    private var prevGesture = "NONE"
    private var grabConfirmed = false
    private var grabStartTime = 0L
    private var lastEventTime = 0L
    private val GRAB_HOLD_MS  = 400L
    private val COOLDOWN_MS   = 600L

    // Server URL from activity
    private var serverUrl = ""

    // ---------------------------------------------------------------- //
    //  Lifecycle                                                        //
    // ---------------------------------------------------------------- //

    override fun onCreate() {
        super.onCreate()
        cameraExecutor = Executors.newSingleThreadExecutor()
        createNotificationChannel()
        initHandLandmarker()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)

        serverUrl = intent?.getStringExtra(EXTRA_SERVER_URL) ?: ""

        // Show persistent notification (required for foreground service)
        startForeground(NOTIFICATION_ID, buildNotification("Initialising..."))

        connectToServer()
        startCamera()

        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        webSocket?.close(1000, "Service stopped")
        handLandmarker?.close()
        cameraExecutor.shutdown()
        Log.i(TAG, "Service destroyed")
    }

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    // ---------------------------------------------------------------- //
    //  MediaPipe Hand Landmarker                                        //
    // ---------------------------------------------------------------- //

    private fun initHandLandmarker() {
        try {
            val baseOptions = BaseOptions.builder()
                .setModelAssetPath("hand_landmarker.task")
                .build()

            val options = HandLandmarker.HandLandmarkerOptions.builder()
                .setBaseOptions(baseOptions)
                .setMinHandDetectionConfidence(0.7f)
                .setMinHandPresenceConfidence(0.5f)
                .setMinTrackingConfidence(0.5f)
                .setNumHands(1)
                .setRunningMode(RunningMode.LIVE_STREAM)
                .setResultListener { result, _ -> processLandmarks(result) }
                .build()

            handLandmarker = HandLandmarker.createFromOptions(this, options)
            Log.i(TAG, "HandLandmarker initialized")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to init HandLandmarker: ${e.message}")
        }
    }

    // ---------------------------------------------------------------- //
    //  Camera (CameraX)                                                 //
    // ---------------------------------------------------------------- //

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()

            val imageAnalysis = ImageAnalysis.Builder()
                .setTargetResolution(android.util.Size(320, 240))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()

            imageAnalysis.setAnalyzer(cameraExecutor) { imageProxy ->
                processImageProxy(imageProxy)
            }

            val cameraSelector = CameraSelector.DEFAULT_FRONT_CAMERA

            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(this, cameraSelector, imageAnalysis)
                Log.i(TAG, "Camera bound to service lifecycle")
            } catch (e: Exception) {
                Log.e(TAG, "Camera binding failed: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    @androidx.camera.core.ExperimentalGetImage
    private fun processImageProxy(imageProxy: ImageProxy) {
        val mediaImage = imageProxy.image ?: run {
            imageProxy.close(); return
        }
        try {
            val bitmap = imageProxy.toBitmap()
            val mpImage = BitmapImageBuilder(bitmap).build()
            handLandmarker?.detectAsync(mpImage, System.currentTimeMillis())
        } catch (e: Exception) {
            Log.e(TAG, "Frame processing error: ${e.message}")
        } finally {
            imageProxy.close()
        }
    }

    // ---------------------------------------------------------------- //
    //  Gesture Classification                                           //
    // ---------------------------------------------------------------- //

    private fun processLandmarks(result: HandLandmarkerResult) {
        if (result.landmarks().isEmpty()) {
            prevGesture   = "NONE"
            grabConfirmed = false
            return
        }

        val lm = result.landmarks()[0]

        // Simple finger-curl check (tip.y > pip.y = curled)
        val indexCurled  = lm[8].y()  > lm[6].y()
        val middleCurled = lm[12].y() > lm[10].y()
        val ringCurled   = lm[16].y() > lm[14].y()
        val pinkyCurled  = lm[20].y() > lm[18].y()

        val currentGesture = if (indexCurled && middleCurled && ringCurled && pinkyCurled)
            "GRAB" else "HOVER"

        handleGestureState(currentGesture)
        prevGesture = currentGesture
    }

    private fun handleGestureState(current: String) {
        val now = System.currentTimeMillis()

        // DROP: was grabbed and now released
        if (current != "GRAB" && grabConfirmed) {
            grabConfirmed = false
            if (now - lastEventTime > COOLDOWN_MS) {
                lastEventTime = now
                onDropGesture()
            }
            return
        }

        // GRAB: confirm after holding 400ms
        if (current == "GRAB") {
            if (!grabConfirmed) {
                if (prevGesture != "GRAB") grabStartTime = now
                if (now - grabStartTime > GRAB_HOLD_MS) {
                    grabConfirmed = true
                    if (now - lastEventTime > COOLDOWN_MS) {
                        lastEventTime = now
                        // No action needed on grab side for Android
                        Log.d(TAG, "Grab confirmed — waiting for DROP")
                        updateNotification("✊ Grab confirmed — open hand to DROP")
                    }
                }
            }
        }
    }

    private fun onDropGesture() {
        Log.i(TAG, "🖐️ DROP gesture detected — requesting content from server")
        updateNotification("🖐️ Dropping content...")
        webSocket?.send("""{"event":"DROP"}""")
    }

    // ---------------------------------------------------------------- //
    //  WebSocket                                                        //
    // ---------------------------------------------------------------- //

    private fun connectToServer() {
        if (serverUrl.isBlank()) {
            Log.e(TAG, "No server URL provided")
            return
        }

        val request = Request.Builder().url(serverUrl).build()

        webSocket = okHttpClient.newWebSocket(request, object : WebSocketListener() {

            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "✅ WebSocket connected")
                broadcastStatus("🟢 Connected to server — make FIST gesture to GRAB, open to DROP", "#00CC00")
                updateNotification("🟢 Connected — make gesture")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val data = JSONObject(text)
                when (data.getString("event")) {
                    "CONTENT_READY" -> {
                        val type = data.optString("type", "content")
                        broadcastStatus("📦 Content ready from Windows ($type) — make DROP gesture!", "#FFA500")
                        updateNotification("📦 Content ready! Open hand to receive")
                    }
                    "RECEIVE" -> {
                        val type    = data.optString("type", "text")
                        val content = data.optString("content", "")
                        Log.i(TAG, "📲 Received [$type]: ${content.take(50)}")
                        broadcastContent(type, content)
                        broadcastStatus("✅ Content received!", "#00CC00")
                        updateNotification("✅ Content dropped successfully!")
                    }
                    "RECEIVE_EMPTY" -> {
                        broadcastStatus("⚠️ Nothing grabbed on Windows yet", "#FF6600")
                        updateNotification("⚠️ Nothing to drop — grab something on PC first")
                    }
                    "STATUS" -> {
                        val devices = data.optJSONArray("devices")
                        Log.i(TAG, "Devices online: $devices")
                    }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket error: ${t.message}")
                broadcastStatus("🔴 Disconnected — retrying...", "#CC0000")
                updateNotification("🔴 Connection lost — retrying...")
                // Reconnect after 3 seconds
                android.os.Handler(mainLooper).postDelayed({ connectToServer() }, 3000)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WebSocket closed: $reason")
            }
        })
    }

    // ---------------------------------------------------------------- //
    //  Broadcasts to Activity                                           //
    // ---------------------------------------------------------------- //

    private fun broadcastContent(type: String, content: String) {
        sendBroadcast(Intent(ACTION_CONTENT_RECEIVED).apply {
            putExtra("type", type)
            putExtra("content", content)
        })
    }

    private fun broadcastStatus(status: String, color: String) {
        sendBroadcast(Intent(ACTION_STATUS_UPDATE).apply {
            putExtra("status", status)
            putExtra("color", color)
        })
    }

    // ---------------------------------------------------------------- //
    //  Notification                                                     //
    // ---------------------------------------------------------------- //

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "GestureSync",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Air gesture detection service"
            lightColor  = Color.GREEN
        }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(channel)
    }

    private fun buildNotification(text: String): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("GestureSync Active")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIFICATION_ID, buildNotification(text))
    }
}
