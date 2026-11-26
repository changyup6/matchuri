import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Font 설정
const paperlogy = localFont({
  src: [
    {
      path: "../fonts/Paperlogy-1Thin.ttf",
      weight: "100",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-2ExtraLight.ttf",
      weight: "200",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-3Light.ttf",
      weight: "300",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-4Regular.ttf",
      weight: "400",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-5Medium.ttf",
      weight: "500",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-6SemiBold.ttf",
      weight: "600",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-7Bold.ttf",
      weight: "700",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-8ExtraBold.ttf",
      weight: "800",
      style: "normal",
    },
    {
      path: "../fonts/Paperlogy-9Black.ttf",
      weight: "900",
      style: "normal",
    },
  ],
  variable: "--font-paperlogy",
});

export const metadata: Metadata = {
  title: "맛추리",
  description: "맛집 추천 서비스",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${paperlogy.variable} h-full`}>
      <body className="bg-background text-foreground flex min-h-full flex-col">
        {children}
      </body>
    </html>
  );
}
