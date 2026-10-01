package com.gesturesync

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import android.Manifest
import android.content.pm.PackageManager
import android.os.Build

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "GestureSync"
        private const val CAMERA_PERMISSION_REQUEST = 100
        private const val NOTIFICATION_PERMISSION_REQUEST = 101
    }

    // UI refs
    private lateinit var btnStart: Button
    private lateinit var btnStop: Button
    private lateinit var editServerUrl: EditText
    private lateinit var tvStatus: TextView
    private lateinit var tvLastContent: TextView
    private lateinit var switchShowCamera: Switch
    private lateinit var cardStatus: View

    private var isServiceRunning = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        bindViews()
        setupListeners()
        checkPermissions()

        // Receive content from service via LocalBroadcast
        registerReceiver(contentReceiver,
            android.content.IntentFilter(GestureDropService.ACTION_CONTENT_RECEIVED),
            RECEIVER_NOT_EXPORTED
        )
        registerReceiver(statusReceiver,
            android.content.IntentFilter(GestureDropService.ACTION_STATUS_UPDATE),
            RECEIVER_NOT_EXPORTED
        )
    }

    // ------------------------------------------------------------------ //
    //  UI binding                                                          //
    // ------------------------------------------------------------------ //

    private fun bindViews() {
        btnStart        = findViewById(R.id.btnStart)
        btnStop         = findViewById(R.id.btnStop)
        editServerUrl   = findViewById(R.id.editServerUrl)
        tvStatus        = findViewById(R.id.tvStatus)
        tvLastContent   = findViewById(R.id.tvLastContent)
        switchShowCamera = findViewById(R.id.switchShowCamera)
        cardStatus      = findViewById(R.id.cardStatus)

        // Default server URL
        editServerUrl.setText("ws://192.168.1.100:8765/ws/android")

        updateServiceButtons(running = false)
    }

    private fun setupListeners() {
        btnStart.setOnClickListener {
            val url = editServerUrl.text.toString().trim()
            if (url.isEmpty()) {
                Toast.makeText(this, "Enter server URL first", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            startGestureService(url)
        }

        btnStop.setOnClickListener {
            stopGestureService()
        }
    }

    // ------------------------------------------------------------------ //
    //  Service control                                                     //
    // ------------------------------------------------------------------ //

    private fun startGestureService(serverUrl: String) {
        val intent = Intent(this, GestureDropService::class.java).apply {
            putExtra(GestureDropService.EXTRA_SERVER_URL, serverUrl)
            putExtra(GestureDropService.EXTRA_SHOW_CAMERA, switchShowCamera.isChecked)
        }
        ContextCompat.startForegroundService(this, intent)
        isServiceRunning = true
        updateServiceButtons(running = true)
        updateStatus("⏳ Connecting to server...", "#FFA500")
        Log.i(TAG, "Service started with URL: $serverUrl")
    }

    private fun stopGestureService() {
        val intent = Intent(this, GestureDropService::class.java)
        stopService(intent)
        isServiceRunning = false
        updateServiceButtons(running = false)
        updateStatus("⭕ Service stopped", "#888888")
    }

    // ------------------------------------------------------------------ //
    //  Receivers from service                                              //
    // ------------------------------------------------------------------ //

    private val contentReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val type    = intent.getStringExtra("type") ?: "unknown"
            val content = intent.getStringExtra("content") ?: ""

            runOnUiThread {
                when (type) {
                    "text" -> {
                        copyToClipboard(content)
                        tvLastContent.text = "📋 Text received & copied:\n\"${content.take(80)}...\""
                        showToast("✅ Text dropped to clipboard!")
                    }
                    "url" -> {
                        copyToClipboard(content)
                        tvLastContent.text = "🔗 URL received:\n$content"
                        showToast("✅ URL copied!")
                    }
                    else -> {
                        tvLastContent.text = "📦 [$type] received"
                        showToast("✅ Content dropped!")
                    }
                }
                // Vibrate on receive
                val vibrator = getSystemService(Context.VIBRATOR_SERVICE) as android.os.Vibrator
                vibrator.vibrate(android.os.VibrationEffect.createOneShot(200, android.os.VibrationEffect.DEFAULT_AMPLITUDE))
            }
        }
    }

    private val statusReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val status  = intent.getStringExtra("status") ?: ""
            val color   = intent.getStringExtra("color") ?: "#FFFFFF"
            runOnUiThread { updateStatus(status, color) }
        }
    }

    // ------------------------------------------------------------------ //
    //  Helpers                                                             //
    // ------------------------------------------------------------------ //

    private fun copyToClipboard(text: String) {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("GestureSync", text))
    }

    private fun updateStatus(msg: String, hexColor: String) {
        tvStatus.text = msg
        tvStatus.setTextColor(android.graphics.Color.parseColor(hexColor))
    }

    private fun updateServiceButtons(running: Boolean) {
        btnStart.isEnabled = !running
        btnStop.isEnabled  = running
        btnStart.alpha     = if (running) 0.4f else 1.0f
        btnStop.alpha      = if (!running) 0.4f else 1.0f
    }

    private fun showToast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }

    // ------------------------------------------------------------------ //
    //  Permissions                                                         //
    // ------------------------------------------------------------------ //

    private fun checkPermissions() {
        val permsNeeded = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED) {
            permsNeeded.add(Manifest.permission.CAMERA)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                permsNeeded.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (permsNeeded.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, permsNeeded.toTypedArray(), CAMERA_PERMISSION_REQUEST)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (grantResults.any { it != PackageManager.PERMISSION_GRANTED }) {
            Toast.makeText(this, "Camera permission is required!", Toast.LENGTH_LONG).show()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        unregisterReceiver(contentReceiver)
        unregisterReceiver(statusReceiver)
    }
}
