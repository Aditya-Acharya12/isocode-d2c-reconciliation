"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, fieldErrors } from "@/lib/api";
import { useCan } from "@/lib/session";
import type { Partner, PartnerType } from "@/lib/types";

const TYPE_LABELS: Record<PartnerType, string> = {
  marketplace: "Marketplace",
  quick_commerce: "Quick commerce",
  courier: "Courier",
  payment_gateway: "Payment gateway",
};
const TYPE_OPTIONS = Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }));
const PARTNERS_KEY = ["partners"];

/** Empty means not provided. Anything else must be a whole number from 0 to 365. */
function dayError(value: string): string | undefined {
  if (!/^\d+$/.test(value.trim())) return "Enter a whole number of days.";
  if (Number(value) > 365) return "Must be 365 days or fewer.";
}

function PartnerDialog({
  partner,
  open,
  onClose,
}: {
  partner: Partner | null;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const isNew = partner === null;
  const [name, setName] = useState(partner?.name ?? "");
  const [type, setType] = useState<PartnerType>(partner?.type ?? "marketplace");
  const [terms, setTerms] = useState(String(partner?.settlement_terms_days ?? 15));
  const [grace, setGrace] = useState(String(partner?.return_grace_days ?? 15));
  const [errors, setErrors] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: () => {
      const days = {
        settlement_terms_days: Number(terms),
        return_grace_days: Number(grace),
      };
      return isNew
        ? api<Partner>("/partners", { method: "POST", body: { name, type, ...days } })
        : api<Partner>(`/partners/${partner.id}`, { method: "PATCH", body: days });
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: PARTNERS_KEY });
      toast.success(isNew ? `${saved.name} added` : `${saved.name} updated`);
      onClose();
    },
    onError: (error) => setErrors(fieldErrors(error)),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const found: Record<string, string> = {};
    if (isNew && name.trim().length < 2) found.name = "Enter a name of at least 2 characters.";
    const termsError = dayError(terms);
    const graceError = dayError(grace);
    if (termsError) found.settlement_terms_days = termsError;
    if (graceError) found.return_grace_days = graceError;
    setErrors(found);
    if (Object.keys(found).length === 0) save.mutate();
  }

  const hasFieldErrors = Object.keys(errors).length > 0;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>{isNew ? "Add partner" : `Edit ${partner.name}`}</DialogTitle>
            <DialogDescription>
              Terms decide when a payment counts as past due, and when a return counts as missing.
            </DialogDescription>
          </DialogHeader>

          {isNew && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="partner-name">Name</Label>
                <Input
                  id="partner-name"
                  value={name}
                  aria-invalid={Boolean(errors.name)}
                  onChange={(event) => setName(event.target.value)}
                />
                {errors.name && <p className="text-xs text-destructive">{errors.name}</p>}
              </div>
              <div className="space-y-1.5">
                <Label>Type</Label>
                <Select
                  items={TYPE_OPTIONS}
                  value={type}
                  onValueChange={(value) => setType(value as PartnerType)}
                >
                  <SelectTrigger className="w-full" aria-label="Partner type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent alignItemWithTrigger={false}>
                    {TYPE_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="partner-terms">Payment terms (days)</Label>
              <Input
                id="partner-terms"
                inputMode="numeric"
                value={terms}
                aria-invalid={Boolean(errors.settlement_terms_days)}
                onChange={(event) => setTerms(event.target.value)}
              />
              {errors.settlement_terms_days && (
                <p className="text-xs text-destructive">{errors.settlement_terms_days}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="partner-grace">Return grace (days)</Label>
              <Input
                id="partner-grace"
                inputMode="numeric"
                value={grace}
                aria-invalid={Boolean(errors.return_grace_days)}
                onChange={(event) => setGrace(event.target.value)}
              />
              {errors.return_grace_days && (
                <p className="text-xs text-destructive">{errors.return_grace_days}</p>
              )}
            </div>
          </div>

          {save.isError && !hasFieldErrors && (
            <p role="alert" className="text-sm text-destructive">
              {save.error.message}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving…" : isNew ? "Add partner" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ActiveToggleDialog({ partner, onClose }: { partner: Partner; onClose: () => void }) {
  const queryClient = useQueryClient();
  const deactivating = partner.is_active;

  const toggle = useMutation({
    mutationFn: () =>
      api<Partner>(`/partners/${partner.id}`, {
        method: "PATCH",
        body: { is_active: !partner.is_active },
      }),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: PARTNERS_KEY });
      toast.success(`${saved.name} ${saved.is_active ? "reactivated" : "deactivated"}`);
      onClose();
    },
  });

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {deactivating ? `Deactivate ${partner.name}?` : `Reactivate ${partner.name}?`}
          </DialogTitle>
          <DialogDescription>
            {deactivating
              ? "Its existing records stay in place, and this can be reversed."
              : "It becomes available for new files again."}
          </DialogDescription>
        </DialogHeader>
        {toggle.isError && (
          <p role="alert" className="text-sm text-destructive">
            {toggle.error.message}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={deactivating ? "destructive" : "default"}
            disabled={toggle.isPending}
            onClick={() => toggle.mutate()}
          >
            {deactivating ? "Deactivate" : "Reactivate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function PartnersPage() {
  const canManage = useCan("manage_config");
  const [editing, setEditing] = useState<{ partner: Partner | null } | null>(null);
  const [toggling, setToggling] = useState<Partner | null>(null);

  const partners = useQuery({
    queryKey: PARTNERS_KEY,
    queryFn: () => api<Partner[]>("/partners"),
  });

  return (
    <>
      <PageHeader
        title="Partners"
        description={
          canManage
            ? "Marketplaces, couriers and payment providers, with their payment terms."
            : "Marketplaces, couriers and payment providers. Only a co-founder can change these."
        }
        actions={
          canManage ? (
            <Button onClick={() => setEditing({ partner: null })}>Add partner</Button>
          ) : undefined
        }
      />

      {partners.isPending && <RowsSkeleton />}
      {partners.isError && (
        <ErrorState error={partners.error} onRetry={() => partners.refetch()} />
      )}
      {partners.isSuccess && partners.data.length === 0 && (
        <EmptyState title="No partners yet">Partners you add will appear here.</EmptyState>
      )}
      {partners.isSuccess && partners.data.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-right">Payment terms</TableHead>
                <TableHead className="text-right">Return grace</TableHead>
                <TableHead>Status</TableHead>
                {canManage && <TableHead className="text-right">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {partners.data.map((partner) => (
                <TableRow key={partner.id} data-testid={`partner-${partner.name}`}>
                  <TableCell className="font-medium">{partner.name}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {TYPE_LABELS[partner.type]}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {partner.settlement_terms_days} days
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {partner.return_grace_days} days
                  </TableCell>
                  <TableCell>
                    <Badge variant={partner.is_active ? "secondary" : "outline"}>
                      {partner.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TableCell>
                  {canManage && (
                    <TableCell className="space-x-1 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditing({ partner })}
                        aria-label={`Edit ${partner.name}`}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setToggling(partner)}
                        aria-label={`${partner.is_active ? "Deactivate" : "Reactivate"} ${partner.name}`}
                      >
                        {partner.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {editing && (
        <PartnerDialog
          key={editing.partner?.id ?? "new"}
          partner={editing.partner}
          open
          onClose={() => setEditing(null)}
        />
      )}
      {toggling && <ActiveToggleDialog partner={toggling} onClose={() => setToggling(null)} />}
    </>
  );
}
