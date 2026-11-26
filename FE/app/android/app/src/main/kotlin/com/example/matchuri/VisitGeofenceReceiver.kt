package com.matchuri

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingEvent
import com.google.android.gms.location.LocationServices

class VisitGeofenceReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val event = GeofencingEvent.fromIntent(intent) ?: return
        if (event.hasError() || event.geofenceTransition != Geofence.GEOFENCE_TRANSITION_ENTER) return
        val prefs = context.getSharedPreferences("visit_notifications", Context.MODE_PRIVATE)
        val payload = prefs.getString("tracked", null) ?: return
        // Consume once: repeated ENTER events must not reschedule the survey.
        prefs.edit().remove("tracked").apply()
        ReviewNotifications.arrived(context, payload, false)
        LocationServices.getGeofencingClient(context).removeGeofences(
            PendingIntent.getBroadcast(
                context, 0, Intent(context, VisitGeofenceReceiver::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0,
            ),
        )
    }
}
