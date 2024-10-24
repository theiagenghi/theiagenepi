import { ColumnDef } from "@tanstack/react-table";
import { CellComponent } from "czifui";
import { memo } from "src/common/utils/memo";
import { generateWidthStyles } from "src/common/utils/tableUtils";
import { SortableHeader } from "src/views/Data/components/SortableHeader";
import { getQCStatusFromSample } from "src/views/Upload/components/Samples/utils";
import { QualityScoreTag } from "../components/QualityScoreTag";

export const qualityControlColumn: ColumnDef<Sample, any> = {
  id: "qualityControl",
  accessorKey: "qcMetrics",
  header: ({ header, column }) => (
    <SortableHeader
      header={header}
      style={generateWidthStyles(column)}
      tooltipStrings={{
        boldText: "Quality Score",
        regularText:
          "Overall QC score from Nextclade which considers genome completion and screens for potential contamination and sequencing or bioinformatics errors.",
        link: {
          href: "https://docs.nextstrain.org/projects/nextclade/en/stable/user/algorithm/06-quality-control.html",
          linkText: "Learn more",
        },
      }}
    >
      Quality Score
    </SortableHeader>
  ),
  cell: memo(({ getValue, cell }) => {
    const qcMetric = getValue()?.[0];
    return (
      <CellComponent key={cell.id}>
        <QualityScoreTag qcMetric={qcMetric} />
      </CellComponent>
    );
  }),
  sortingFn: (a, b) => {
    const statusA = getQCStatusFromSample(a.original) ?? "";
    const statusB = getQCStatusFromSample(b.original) ?? "";
    return statusA > statusB ? -1 : 1;
  },
};
