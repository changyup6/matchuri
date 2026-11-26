package com.matchuri

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.SystemClock
import android.os.Build
import org.json.JSONObject

object ReviewNotifications {
    const val EXTRA = "visit_review"
    private const val CHANNEL = "visit_reviews"

    fun arrived(context: Context, payload: String, simulated: Boolean) {
        val data = JSONObject(payload)
        data.put("simulated", simulated)
        if (!simulated) data.put("arrived", true)
        val updated = data.toString()
        notify(context, updated, false)
        if (simulated) {
            notify(context, updated, true)
        } else {
            // Inexact alarm survives process death; Android may defer in Doze.
            val alarm = context.getSystemService(AlarmManager::class.java)
            val intent = Intent(context, ReviewReminderReceiver::class.java)
                .setData(Uri.parse("matchuri://review-reminder/" + data.getString("visitId")))
                .putExtra(EXTRA, updated)
            val pending = PendingIntent.getBroadcast(context, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            alarm.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                SystemClock.elapsedRealtime() + 40 * 60 * 1000L, pending)
        }
    }

    fun notify(context: Context, payload: String, review: Boolean) {
        val data = JSONObject(payload)
        val manager = context.getSystemService(NotificationManager::class.java)
        if (!manager.areNotificationsEnabled()) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(NotificationChannel(
                CHANNEL, "방문 및 리뷰 알림", NotificationManager.IMPORTANCE_HIGH))
        }
        val name = data.getString("storeName")
        val test = if (data.optBoolean("simulated")) "[테스트] " else ""
        val builder = (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) Notification.Builder(context, CHANNEL)
            else Notification.Builder(context))
            .setSmallIcon(R.drawable.ic_visit_notification)
            .setContentTitle(test + if (review) "식사는 어떠셨나요?" else "식당 근처에 도착했어요")
            .setContentText(if (review) "$name 방문 이야기를 남겨주세요." else "$name 근처예요. 맛있는 시간 되세요!")
            .setAutoCancel(true)
        if (review) {
            val intent = Intent(context, MainActivity::class.java)
                .setAction("com.matchuri.OPEN_REVIEW")
                .setData(Uri.parse("matchuri://review/" + data.getString("visitId")))
                .putExtra(EXTRA, payload)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            builder.setContentIntent(PendingIntent.getActivity(context, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        }
        manager.notify(data.getString("visitId"), if (review) 1002 else 1001, builder.build())
    }
}

class ReviewReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val payload = intent.getStringExtra(ReviewNotifications.EXTRA) ?: return
        ReviewNotifications.notify(context, payload, true)
    }
}
