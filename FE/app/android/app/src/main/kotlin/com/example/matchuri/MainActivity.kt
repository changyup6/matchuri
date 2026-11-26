package com.matchuri

import android.Manifest
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import android.provider.Settings
import android.net.Uri
import androidx.core.content.ContextCompat
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingClient
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationServices
import com.google.android.gms.common.api.ApiException
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONObject

class MainActivity : FlutterActivity() {
    private val client: GeofencingClient by lazy { LocationServices.getGeofencingClient(this) }
    private var openBackgroundSettingsAfterNotification = false
    private var visitChannel: MethodChannel? = null
    private var testPermissionResult: MethodChannel.Result? = null
    private var testPayload: String? = null

    private fun captureReviewIntent(value: Intent?) {
        val payload = value?.getStringExtra(ReviewNotifications.EXTRA) ?: return
        getSharedPreferences("visit_notifications", MODE_PRIVATE).edit()
            .putString("launch", payload).apply()
        value.removeExtra(ReviewNotifications.EXTRA)
        visitChannel?.invokeMethod("reviewAvailable", null)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        captureReviewIntent(intent)
    }

    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        captureReviewIntent(intent)
        visitChannel = MethodChannel(engine.dartExecutor.binaryMessenger, "matchuri/visit_geofence")
        visitChannel!!.setMethodCallHandler { call, result ->
                if (call.method == "consumeReviewLaunch") {
                    val prefs = getSharedPreferences("visit_notifications", MODE_PRIVATE)
                    val payload = prefs.getString("launch", null)
                    prefs.edit().remove("launch").apply()
                    return@setMethodCallHandler result.success(payload)
                }
                if (call.method == "simulateVisit") {
                    val payload = JSONObject(call.arguments as Map<*, *>).toString()
                    if (Build.VERSION.SDK_INT >= 33 &&
                        ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                        if (testPermissionResult != null) return@setMethodCallHandler result.success("busy")
                        testPermissionResult = result
                        testPayload = payload
                        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2003)
                    } else if (!getSystemService(android.app.NotificationManager::class.java).areNotificationsEnabled()) {
                        result.success("notifications")
                    } else {
                        ReviewNotifications.arrived(this, payload, true)
                        result.success("registered")
                    }
                    return@setMethodCallHandler
                }
                if (call.method == "openSettings") {
                    startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null)))
                    return@setMethodCallHandler result.success(null)
                }
                if (call.method == "requestVisitPermissions") {
                    requestVisitPermissions()
                    return@setMethodCallHandler result.success(null)
                }
                if (call.method != "register") return@setMethodCallHandler result.notImplemented()
                val latitude = call.argument<Double>("latitude")
                val longitude = call.argument<Double>("longitude")
                val permissionError = permissionError()
                if (latitude == null || longitude == null || permissionError != null) {
                    return@setMethodCallHandler result.success(permissionError ?: "registrationFailed")
                }
                val fence = Geofence.Builder()
                    .setRequestId("visit")
                    .setCircularRegion(latitude, longitude, 150f)
                    .setTransitionTypes(Geofence.GEOFENCE_TRANSITION_ENTER)
                    .setExpirationDuration(24 * 60 * 60 * 1000L)
                    .build()
                val request = GeofencingRequest.Builder()
                    .setInitialTrigger(Geofence.GEOFENCE_TRANSITION_ENTER)
                    .addGeofence(fence)
                    .build()
                client.removeGeofences(pendingIntent()).addOnCompleteListener {
                    client.addGeofences(request, pendingIntent())
                        .addOnSuccessListener {
                            getSharedPreferences("visit_notifications", MODE_PRIVATE).edit()
                                .putString("tracked", JSONObject(call.arguments as Map<*, *>).toString()).apply()
                            result.success("registered")
                        }
                        .addOnFailureListener { error ->
                            val code = (error as? ApiException)?.statusCode
                            Log.e("VisitGeofence", "Registration failed", error)
                            result.success("registrationFailed:${code ?: error.javaClass.simpleName}")
                        }
                }
            }
    }

    private fun permissionError(): String? {
        val location = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val background = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED
        val notifications = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        return when {
            !location -> "fineLocation"
            !background -> "backgroundLocation"
            !notifications -> "notifications"
            else -> null
        }
    }

    private fun requestVisitPermissions() {
        val needsBackground = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_BACKGROUND_LOCATION) != PackageManager.PERMISSION_GRANTED
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            openBackgroundSettingsAfterNotification = needsBackground
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2001)
        } else if (needsBackground) {
            requestBackgroundLocation()
        }
    }

    private fun requestBackgroundLocation() {
        if (Build.VERSION.SDK_INT == Build.VERSION_CODES.Q) {
            requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), 2002)
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null)))
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 2003) {
            val callback = testPermissionResult
            val payload = testPayload
            testPermissionResult = null
            testPayload = null
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED && payload != null) {
                ReviewNotifications.arrived(this, payload, true)
                callback?.success("registered")
            } else callback?.success("notifications")
        }
        if (requestCode == 2001 && openBackgroundSettingsAfterNotification) {
            openBackgroundSettingsAfterNotification = false
            requestBackgroundLocation()
        }
    }

    private fun pendingIntent(): PendingIntent = PendingIntent.getBroadcast(
        this,
        0,
        Intent(this, VisitGeofenceReceiver::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0,
    )
}
