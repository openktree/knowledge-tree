import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SourceInsightsResponse } from "@/types";

interface InsightsSummaryCardsProps {
  data: SourceInsightsResponse;
}

export function InsightsSummaryCards({ data }: InsightsSummaryCardsProps) {
  const failRate =
    data.total_count > 0
      ? ((data.failed_count / data.total_count) * 100).toFixed(1)
      : "0";

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Failed Fetches
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold text-destructive">
            {data.failed_count.toLocaleString()}
          </div>
          <p className="text-xs text-muted-foreground">{failRate}% of total</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Pending Super Sources
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {data.pending_super_count.toLocaleString()}
          </div>
          <p className="text-xs text-muted-foreground">awaiting fetch</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Total Sources
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {data.total_count.toLocaleString()}
          </div>
          <p className="text-xs text-muted-foreground">
            {data.top_failed_domains.length} domain{data.top_failed_domains.length !== 1 ? "s" : ""} with failures
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
