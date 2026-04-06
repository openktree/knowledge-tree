"use client";

import { useState } from "react";
import { AlertCircle, Check, ChevronsUpDown, Loader2, User } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { MemberResponse } from "@/types";

interface MemberSearchProps {
  onSelect: (member: MemberResponse) => void;
  excludeUserIds?: string[];
}

export function MemberSearch({ onSelect, excludeUserIds = [] }: MemberSearchProps) {
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<MemberResponse[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<MemberResponse | null>(null);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next && !loaded && !loading) {
      setLoading(true);
      setError(null);
      api.members
        .list()
        .then((data) => {
          setMembers(data);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : "Failed to load users";
          if (msg.includes("403")) {
            setError("Insufficient permissions to search users. Ask a superuser to add members.");
          } else {
            setError(msg);
          }
        })
        .finally(() => {
          setLoading(false);
          setLoaded(true);
        });
    }
  };

  const excludeSet = new Set(excludeUserIds);
  const filteredMembers = members.filter((m) => !excludeSet.has(m.id));

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="justify-between text-sm h-9 w-full max-w-sm font-normal"
        >
          {selected ? (
            <span className="truncate">{selected.email}</span>
          ) : (
            <span className="text-muted-foreground">Search users...</span>
          )}
          <ChevronsUpDown className="ml-2 size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="start">
        <Command>
          <CommandInput placeholder="Search by email or name..." />
          <CommandList>
            {loading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            ) : error ? (
              <div className="flex items-center gap-2 px-3 py-4 text-sm text-muted-foreground">
                <AlertCircle className="size-4 shrink-0 text-destructive" />
                <span>{error}</span>
              </div>
            ) : (
              <>
                <CommandEmpty>No users found.</CommandEmpty>
                <CommandGroup>
                  {filteredMembers.map((m) => (
                    <CommandItem
                      key={m.id}
                      value={m.email + " " + (m.display_name ?? "")}
                      onSelect={() => {
                        setSelected(m);
                        onSelect(m);
                        setOpen(false);
                      }}
                    >
                      <Check
                        className={cn(
                          "mr-2 size-3.5",
                          selected?.id === m.id ? "opacity-100" : "opacity-0",
                        )}
                      />
                      <User className="mr-2 size-3.5 text-muted-foreground" />
                      <div className="min-w-0">
                        <p className="text-sm truncate">{m.email}</p>
                        {m.display_name && (
                          <p className="text-xs text-muted-foreground truncate">
                            {m.display_name}
                          </p>
                        )}
                      </div>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
