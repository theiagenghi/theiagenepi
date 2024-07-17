import { ColumnDef } from "@tanstack/react-table";
import { memo } from "src/common/utils/memo";
import { generateWidthStyles } from "src/common/utils/tableUtils";
import { SortableHeader } from "src/views/Data/components/SortableHeader";
import { TreeTypeTooltip } from "../components/TreeTypeTooltip";
import { StyledCellBasic } from "../style";

export const treeType: ColumnDef<PhyloRun, any> = {
  id: "treeType",
  accessorKey: "treeType",
  size: 160,
  header: ({ header, column }) => (
    <SortableHeader
      header={header}
      style={generateWidthStyles(column)}
      tooltipStrings={{
        boldText: "Tree Type",
        link: {
          href: "https://theiagengenomics5614.zendesk.com/hc/en-us/article_attachments/27402419300507",
          linkText: "Read our guide to learn more.",
        },
        regularText:
          "TheiaGenEpi-defined profiles for tree building based on primary use case and build settings.",
      }}
    >
      Tree Type
    </SortableHeader>
  ),
  cell: memo(({ getValue, cell }) => {
    const type = getValue();
    return (
      <TreeTypeTooltip value={type}>
        <StyledCellBasic
          key={cell.id}
          verticalAlign="center"
          shouldShowTooltipOnHover={false}
          primaryText={getValue()}
        />
      </TreeTypeTooltip>
    );
  }),
  enableSorting: true,
};
