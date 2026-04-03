import type { ErrorGroupCount } from "@/types";

interface CommonErrorsTableProps {
  data: ErrorGroupCount[];
}

export function CommonErrorsTable({ data }: CommonErrorsTableProps) {
  if (data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4 text-center">
        No fetch errors in this period.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 font-medium text-muted-foreground">Error Message</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">Count</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i} className="border-b last:border-0">
              <td
                className="py-2 max-w-[400px] truncate font-mono text-xs"
                title={row.error_group}
              >
                {row.error_group}
              </td>
              <td className="py-2 text-right tabular-nums">
                {row.count.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
