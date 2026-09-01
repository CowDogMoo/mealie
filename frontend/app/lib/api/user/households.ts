import { BaseCRUDAPIReadOnly } from "../base/base-clients";
import type { PaginationData } from "../types/non-generated";
import type { QueryValue } from "../base/route";
import type { HouseholdUserFeedbackOut, HouseholdUserRatingOut, UserOut } from "~/lib/api/types/user";
import type {
  HouseholdInDB,
  HouseholdStatistics,
  ReadHouseholdPreferences,
  SetPermissions,
  UpdateHouseholdPreferences,
  CreateInviteToken,
  ReadInviteToken,
  HouseholdSummary,
  HouseholdRecipeSummary,
} from "~/lib/api/types/household";

/** Filters for `GET /api/households/feedback`. Each one only narrows the caller's own household. */
export interface HouseholdFeedbackParams {
  recipeId?: string;
  /** ISO-8601 instant; only events created at or after it are returned. */
  since?: string;
  vote?: string;
}

export interface HouseholdFeedbackListOut {
  feedback: HouseholdUserFeedbackOut[];
}

export interface HouseholdRatingsListOut {
  ratings: HouseholdUserRatingOut[];
}

const prefix = "/api";

const routes = {
  households: `${prefix}/groups/households`,
  householdsSelf: `${prefix}/households/self`,
  members: `${prefix}/households/members`,
  permissions: `${prefix}/households/permissions`,
  feedback: `${prefix}/households/feedback`,
  ratings: `${prefix}/households/ratings`,

  preferences: `${prefix}/households/preferences`,
  statistics: `${prefix}/households/statistics`,
  invitation: `${prefix}/households/invitations`,

  householdsId: (id: string | number) => `${prefix}/groups/households/${id}`,
  householdsSelfRecipesSlug: (recipeSlug: string) => `${prefix}/households/self/recipes/${recipeSlug}`,
};

export class HouseholdAPI extends BaseCRUDAPIReadOnly<HouseholdSummary> {
  baseRoute = routes.households;
  itemRoute = routes.householdsId;
  /** Returns the Household Data for the Current User
   */
  async getCurrentUserHousehold() {
    return await this.requests.get<HouseholdInDB>(routes.householdsSelf);
  }

  async getCurrentUserHouseholdRecipe(recipeSlug: string) {
    return await this.requests.get<HouseholdRecipeSummary>(routes.householdsSelfRecipesSlug(recipeSlug));
  }

  async setPreferences(payload: UpdateHouseholdPreferences) {
    // TODO: This should probably be a patch request, which isn't offered by the API currently
    return await this.requests.put<ReadHouseholdPreferences, UpdateHouseholdPreferences>(routes.preferences, payload);
  }

  async createInvitation(payload: CreateInviteToken) {
    return await this.requests.post<ReadInviteToken>(routes.invitation, payload);
  }

  async fetchMembers(page = 1, perPage = -1, params = {} as Record<string, QueryValue>) {
    return await this.requests.get<PaginationData<UserOut>>(routes.members, { page, perPage, ...params });
  }

  async setMemberPermissions(payload: SetPermissions) {
    // TODO: This should probably be a patch request, which isn't offered by the API currently
    return await this.requests.put<UserOut, SetPermissions>(routes.permissions, payload);
  }

  async statistics() {
    return await this.requests.get<HouseholdStatistics>(routes.statistics);
  }

  /** Every household member's feedback events, oldest first. Read-only; writes stay self-only. */
  async getHouseholdFeedback(params: HouseholdFeedbackParams = {}) {
    // an unset filter must not travel as the literal string "undefined"
    const query = Object.fromEntries(
      Object.entries(params).filter(([_, v]) => v !== null && v !== undefined),
    ) as Record<string, QueryValue>;

    return await this.requests.get<HouseholdFeedbackListOut>(routes.feedback, query);
  }

  /** Every household member's star ratings, grouped by member in username order. */
  async getHouseholdRatings() {
    return await this.requests.get<HouseholdRatingsListOut>(routes.ratings);
  }
}
