"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import type { SystemSettingsResponse } from "@/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<SystemSettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchSettings = useCallback(async () => {
    try {
      const data = await api.systemSettings.get();
      setSettings(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const handleToggle = async (checked: boolean) => {
    setSaving(true);
    try {
      const updated = await api.systemSettings.update({
        disable_self_registration: checked,
      });
      setSettings(updated);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-sm text-muted-foreground">Loading settings...</p>
      </div>
    );
  }

  const isEnvOverride = settings?.disable_self_registration_source === "env";

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <h1 className="text-xl font-semibold mb-6">Settings</h1>

      <div className="rounded-xl border border-border bg-card p-6">
        <h2 className="text-sm font-medium mb-4 text-muted-foreground uppercase tracking-wide">
          Registration
        </h2>

        <div className="flex items-center justify-between">
          <div className="flex-1">
            <p className="text-sm font-medium">Disable self-registration</p>
            <p className="text-sm text-muted-foreground mt-1">
              When enabled, new users cannot create accounts. Admins can still add users.
            </p>
            {isEnvOverride && (
              <Badge variant="outline" className="mt-2">
                Overridden by environment variable
              </Badge>
            )}
          </div>
          <Switch
            checked={settings?.disable_self_registration ?? false}
            onCheckedChange={handleToggle}
            disabled={isEnvOverride || saving}
          />
        </div>
      </div>
    </div>
  );
}
