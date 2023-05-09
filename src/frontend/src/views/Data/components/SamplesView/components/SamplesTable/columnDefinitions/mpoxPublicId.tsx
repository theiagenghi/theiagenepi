import { ColumnDef } from "@tanstack/react-table";
import { generateWidthStyles } from "src/common/utils/tableUtils";
import { SortableHeader } from "src/views/Data/components/SortableHeader";
import DefaultCell from "../components/DefaultCell";

export const mpoxPublicIdColumn: ColumnDef<Sample, any> = {
  id: "publicId",
  accessorKey: "publicId",
  header: ({ header, column }) => (
    <SortableHeader
      header={header}
      style={generateWidthStyles(column)}
      tooltipStrings={{
        boldText: "Public ID",
        regularText:
          "GenBank Accession ID or public ID generated by CZ GEN EPI.",
      }}
    >
      Public ID
    </SortableHeader>
  ),
  cell: DefaultCell,
  enableSorting: true,
};
