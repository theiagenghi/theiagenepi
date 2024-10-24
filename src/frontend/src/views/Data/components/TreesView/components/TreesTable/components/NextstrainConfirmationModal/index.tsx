import { useSelector } from "react-redux";
import {
  AnalyticsTreeViewNextstrain,
  EVENT_TYPES,
} from "src/common/analytics/eventTypes";
import { analyticsTrackEvent } from "src/common/analytics/methods";
import { NewTabLink } from "src/common/components/library/NewTabLink";
import nextstrainLogo from "src/common/images/nextstrain.png";
import { selectCurrentPathogen } from "src/common/redux/selectors";
import { ConfirmButton } from "src/views/Data/components/ConfirmButton";
import { RedirectConfirmationModal } from "src/views/Data/components/RedirectConfirmationModal";

interface Props {
  open: boolean;
  onClose: () => void;
  treeId: number;
}

const NextstrainConfirmationModal = ({
  open,
  onClose,
  treeId,
}: Props): JSX.Element => {
  const pathogen = useSelector(selectCurrentPathogen);
  const content = (
    <>
      By clicking “Continue” you agree to send a copy of your tree JSON to
      Nextstrain’s visualization service. Nextstrain is a separate service from
      TheiaGenEpi.{" "}
      <NewTabLink href="https://nextstrain.org/">Learn More</NewTabLink>
    </>
  );

  const confirmButton = (
    <ConfirmButton
      treeId={treeId}
      outgoingDestination="nextstrain"
      onClick={() =>
        analyticsTrackEvent<AnalyticsTreeViewNextstrain>(
          EVENT_TYPES.TREE_VIEW_NEXTSTRAIN,
          {
            tree_id: treeId,
            pathogen: pathogen,
          }
        )
      }
    />
  );

  return (
    <RedirectConfirmationModal
      content={content}
      customConfirmButton={confirmButton}
      img={nextstrainLogo}
      isOpen={open}
      onClose={onClose}
      onConfirm={onClose}
      logoWidth={180}
    />
  );
};

export default NextstrainConfirmationModal;
