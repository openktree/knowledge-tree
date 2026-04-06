import type { DomainFailureCount } from "@/types";

interface TopFailedDomainsTableProps {
  data: DomainFailureCount[];
}

export function TopFailedDomainsTable({ data }: TopFailedDomainsTableProps) {
  if (data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4 text-center">
        No failed domains in this period.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 font-medium text-muted-foreground">Domain</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">Failures</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.domain} className="border-b last:border-0">
              <td className="py-2 max-w-[300px] truncate" title={row.domain}>
                {row.domain}
              </td>
              <td className="py-2 text-right tabular-nums">
                {row.failure_count.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
