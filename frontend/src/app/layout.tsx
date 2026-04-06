import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthProvider } from "@/contexts/auth";
import { GraphProvider } from "@/contexts/graph";
import { RouteGuard } from "@/components/auth/RouteGuard";
import { ThemeProvider } from "@/components/theme/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Knowledge Tree",
  description:
    "A knowledge integration system that builds understanding from raw external data",
  icons: {
    icon: "/favicon.ico",
    apple: "/apple-touch-icon.png",
  },
  openGraph: {
    title: "Knowledge Tree",
    description:
      "A knowledge integration system that builds understanding from raw external data",
    images: ["/og-image.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <ThemeProvider>
          <AuthProvider>
            <GraphProvider>
              <RouteGuard>{children}</RouteGuard>
              <Toaster />
            </GraphProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
