import { useTreatments } from "@splitsoftware/splitio-react";
import { Button, Icon, InputText, Link } from "czifui";
import { useRouter } from "next/router";
import { ChangeEvent, useEffect, useState } from "react";
import { useUpdateUserInfo, useUserInfo } from "src/common/queries/auth";
import { H1, H2, P } from "src/common/styles/basicStyle";
import { isUserFlagOn } from "src/components/Split";
import { USER_FEATURE_FLAGS } from "src/components/Split/types";
import {
  GrayIconWrapper,
  StyledDivider,
  StyledH3,
  StyledHeaderRow,
  StyledRow,
  StyledSection,
  SubText,
  WhiteIconWrapper,
} from "./style";

const UNSAVED_CHANGES_MESSAGE =
  "Leave without saving? If you leave, your changes will be canceled and your work will not be saved.";

export default function Account(): JSX.Element {
  const router = useRouter();

  const flag = useTreatments([USER_FEATURE_FLAGS.internal_user]);
  const isInternalUserFlagOn = isUserFlagOn(
    flag,
    USER_FEATURE_FLAGS.internal_user
  );

  const { data: userInfo } = useUserInfo();
  const { mutate: updateUserInfo } = useUpdateUserInfo();

  const [gisaidId, setGisaidId] = useState("");
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState<boolean>(false);

  useEffect(() => {
    if (userInfo?.gisaidSubmitterId) {
      setGisaidId(userInfo.gisaidSubmitterId);
    }
  }, [userInfo?.gisaidSubmitterId]);

  // prompt the user if they try to close the page with unsaved changes
  useEffect(() => {
    const handleWindowClose = (e: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges) return;

      /**
       * (ehoops): The custom message doesn't work, but we still need to
       * assign returnValue for prompt to happen - same as upload page
       * https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event
       */
      e.preventDefault();
      e.returnValue = UNSAVED_CHANGES_MESSAGE;
      return UNSAVED_CHANGES_MESSAGE;
    };

    window.addEventListener("beforeunload", handleWindowClose);

    return () => {
      window.removeEventListener("beforeunload", handleWindowClose);
    };
  }, [hasUnsavedChanges]);

  // prompt the user if they try to change routes with unsaved changes
  useEffect(() => {
    const handleRouteChangeStart = () => {
      if (!hasUnsavedChanges) return;

      if (window.confirm(UNSAVED_CHANGES_MESSAGE)) {
        return;
      } else {
        router.events.emit("routeChangeError");
        throw "routeChange aborted.";
      }
    };

    router.events.on("routeChangeStart", handleRouteChangeStart);

    return () => {
      router.events.off("routeChangeStart", handleRouteChangeStart);
    };
  }, [router.events, hasUnsavedChanges]);

  const handleSave = () => {
    updateUserInfo({ gisaid_submitter_id: gisaidId });
    setHasUnsavedChanges(false);
  };

  const handleNewIdInput = (e: ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setGisaidId(value);
    setHasUnsavedChanges(true);
  };

  return (
    <>
      <StyledHeaderRow>
        <H1>My Account</H1>
        <Button
          onClick={handleSave}
          sdsStyle="rounded"
          sdsType="primary"
          disabled={!hasUnsavedChanges}
          startIcon={
            <>
              {hasUnsavedChanges ? (
                <WhiteIconWrapper>
                  <Icon sdsIcon="save" sdsSize="l" sdsType="static" />
                </WhiteIconWrapper>
              ) : (
                <GrayIconWrapper>
                  <Icon sdsIcon="checkCircle" sdsSize="l" sdsType="static" />
                </GrayIconWrapper>
              )}
            </>
          }
        >
          {hasUnsavedChanges ? "Save" : "Saved"}
        </Button>
      </StyledHeaderRow>
      <StyledDivider />
      <StyledSection>
        <H2>Details</H2>
        <StyledRow>
          <StyledH3>GISAID User Name</StyledH3>
          <SubText>Optional</SubText>
        </StyledRow>
        <StyledRow>
          <P>
            Your personal GISAID user name. This info is used to help prepare
            samples for GISAID submission.
            <span>
              &nbsp;
              <Link
                href="https://help.theiagenepi.org/hc/en-us/articles/8179880474260-Download-data-and-upload-it-into-the-GISAID-data-repository"
                target="_blank"
                rel="noreferrer"
              >
                Learn More.
              </Link>
            </span>
          </P>
        </StyledRow>
        <InputText
          id="gisaid-id-input"
          label="GISAID User Name"
          placeholder="GISAID User Name"
          hideLabel
          value={gisaidId}
          onChange={handleNewIdInput}
        />
      </StyledSection>
      {isInternalUserFlagOn && (
        <StyledSection>
          <StyledH3>TheiaGenEpi Internal User account</StyledH3>
          <SubText>This section is not shown to external users.</SubText>
        </StyledSection>
      )}
    </>
  );
}
