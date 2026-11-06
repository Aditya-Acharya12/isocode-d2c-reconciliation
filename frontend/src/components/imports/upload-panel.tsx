"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { UploadIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { toast } from "sonner";

import { NativeSelect } from "@/components/native-select";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import {
  uploadFile,
  type FileType,
  type IngestionSchema,
  type UploadedFile,
} from "@/lib/ingestion";
import type { Partner } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCEPTED = /\.(csv|xlsx)$/i;

export function UploadPanel({ schema }: { schema: IngestionSchema }) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileType, setFileType] = useState<FileType | "">("");
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  const partners = useQuery({
    queryKey: ["partners"],
    queryFn: () => api<Partner[]>("/partners"),
  });
  const channels = useQuery({
    queryKey: ["sales-channels"],
    queryFn: () => api<{ id: number; code: string; name: string }[]>("/sales-channels"),
  });

  const selected = schema.file_types.find((item) => item.file_type === fileType);
  const sourceOptions = !selected
    ? []
    : selected.uses_channel
      ? (channels.data ?? []).map((channel) => ({ id: channel.id, name: channel.name }))
      : (partners.data ?? []).filter(
          (partner) => partner.is_active && selected.allowed_partner_types.includes(partner.type),
        );

  const upload = useMutation({
    mutationFn: () =>
      uploadFile<UploadedFile>(file as File, {
        file_type: fileType,
        [selected?.uses_channel ? "channel_id" : "partner_id"]: source,
      }),
    onSuccess: (created) => {
      toast.success(`${created.filename} uploaded`);
      router.push(`/imports/${created.id}`);
    },
  });

  function choose(next: File | undefined) {
    upload.reset();
    if (!next) return;
    if (!ACCEPTED.test(next.name)) {
      setFile(null);
      setFileError("Only .csv and .xlsx files are supported.");
      return;
    }
    setFileError(null);
    setFile(next);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    upload.mutate();
  }

  const duplicateOf =
    upload.error instanceof ApiError && upload.error.code === "duplicate_file"
      ? (upload.error.details as { file_id?: number } | undefined)?.file_id
      : undefined;

  return (
    <form onSubmit={submit} className="space-y-4 rounded-md border p-4 md:p-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="file-type">What&apos;s in this file?</Label>
          <NativeSelect
            id="file-type"
            value={fileType}
            onChange={(event) => {
              setFileType(event.target.value as FileType | "");
              setSource("");
            }}
          >
            <option value="">Choose a category</option>
            {schema.file_types.map((item) => (
              <option key={item.file_type} value={item.file_type}>
                {item.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="file-source">
            {selected?.uses_channel ? "Sales channel" : "Partner"}
          </Label>
          <NativeSelect
            id="file-source"
            value={source}
            disabled={!selected}
            onChange={(event) => setSource(event.target.value)}
          >
            <option value="">{selected ? "Choose one" : "Choose a category first"}</option>
            {sourceOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </NativeSelect>
          {selected && partners.isSuccess && channels.isSuccess && sourceOptions.length === 0 && (
            <p className="text-xs text-destructive">
              No active partner can send this kind of file. A co-founder can add one under
              Partners.
            </p>
          )}
        </div>
      </div>

      <div
        data-testid="drop-zone"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "rounded-md border border-dashed px-4 py-8 text-center transition-colors",
          dragging ? "border-foreground bg-muted" : "bg-muted/30",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx"
          aria-label="File to upload"
          className="sr-only"
          onChange={(event) => choose(event.target.files?.[0])}
        />
        {file ? (
          <div className="flex items-center justify-center gap-2 text-sm">
            <span className="min-w-0 truncate font-medium" data-testid="chosen-file">
              {file.name}
            </span>
            <span className="shrink-0 text-muted-foreground">{formatBytes(file.size)}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="Remove this file"
              onClick={() => {
                setFile(null);
                upload.reset();
                if (inputRef.current) inputRef.current.value = "";
              }}
            >
              <XIcon />
            </Button>
          </div>
        ) : (
          <>
            <UploadIcon aria-hidden className="mx-auto mb-2 size-5 text-muted-foreground" />
            <p className="text-sm">
              Drag a file here, or{" "}
              <button
                type="button"
                className="font-medium underline underline-offset-3"
                onClick={() => inputRef.current?.click()}
              >
                browse
              </button>
            </p>
            <p className="mt-1 text-xs text-muted-foreground">CSV or XLSX, up to 25 MB</p>
          </>
        )}
      </div>

      {fileError && (
        <p role="alert" className="text-sm text-destructive">
          {fileError}
        </p>
      )}
      {upload.isError && (
        <p role="alert" className="text-sm text-destructive" data-testid="upload-error">
          {upload.error.message}{" "}
          {duplicateOf && (
            <Link href={`/imports/${duplicateOf}`} className="underline underline-offset-3">
              Open that upload
            </Link>
          )}
        </p>
      )}

      <div className="flex justify-end">
        <Button type="submit" disabled={!file || !fileType || !source || upload.isPending}>
          {upload.isPending ? "Uploading…" : "Upload and continue"}
        </Button>
      </div>
    </form>
  );
}
