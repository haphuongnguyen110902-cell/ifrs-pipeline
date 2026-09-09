import yaml

# Classification for every genuinely new concept across all downloaded
# companies (LVMH, Essity, Shell, Kering, Danone, plus a couple of L'Oreal
# extras and one loreal: variant tag). Keyed by tag WITHOUT its prefix.
new_classification = {
    # --- LVM: (LVMH) extension tags ---
    "AdjustmentsForDepreciationRightOfUseAssets": ("cash_flow", "Adjustments for Depreciation of Right-of-use Assets"),
    "AdjustmentsForProvisionsAndAdjustmentsForDepreciationAndAmortisationExpenseAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss": ("cash_flow", "D&A, Provisions and Impairment Adjustments"),
    "CommitmentsToPurchaseNonControllingInterestsEquity": ("other", "Commitments to Purchase Non-controlling Interests (Equity Impact)"),
    "CostOfNetDebt": ("income_statement", "Cost of Net Debt"),
    "CurrentProvisionsAndOtherCurrentLiabilities": ("balance_sheet", "Current Provisions and Other Current Liabilities"),
    "GainsLossesOnChangeInValueOfTimeValueOfOptionsBeforeTaxGainsLossesOnChangeInValueOfForwardElementsOfForwardContractsBeforeTaxAndGainsLossesOnChangeInValueOfForeignCurrencyBasisSpreadsBeforeTax": ("other", "OCI before Tax - Hedge Cost/Time Value Components"),
    "GainsLossesOnExchangeDifferencesOnTranslationBeforeTaxAndGainsLossesOnHedgesOfNetInvestmentsInForeignOperationsBeforeTax": ("other", "OCI before Tax - Translation & Net Investment Hedges"),
    "IncomeTaxRelatingToChangeInValueOfTimeValueOfOptionsOfOtherComprehensiveIncomeIncomeTaxRelatingToChangeInValueOfForwardElementsOfForwardContractsOfOtherComprehensiveIncomeAndIncomeTaxRelatingToChangeInValueOfForeignCurrencyBasisSpreadsOfOtherComprehensiveIncome": ("other", "Income Tax on Hedge Cost/Time Value Components (OCI)"),
    "IncreaseDecreaseInCurrentFinancialAssetsAvailableForSaleClassifiedAsFinancingActivities": ("cash_flow", "Change in Current Available-for-sale Financial Assets"),
    "IncreaseDecreaseInEquityClassifiedAsFinancingActivities": ("cash_flow", "Other Change in Equity (Financing)"),
    "IncreaseDecreaseInNumberOfSharesIssuedThroughCancellationOfTreasuryShares": ("other", "Change in Shares Issued via Cancellation of Treasury Shares"),
    "IncreaseDecreaseThroughAcquisitionOfSubsidiaryAndIncreaseDecreaseThroughDisposalOfSubsidiary": ("other", "Change in Equity from Acquisition/Disposal of Subsidiary"),
    "InterestPaidOnLeaseLiabilitiesClassifiedAsOperatingActivities": ("cash_flow", "Interest Paid on Lease Liabilities"),
    "InterestPaidOtherThanLeaseLiabilitiesClassifiedAsOperatingActivities": ("cash_flow", "Interest Paid (excluding Leases)"),
    "NonCurrentCommitmentsToPurchaseNonControllingInterests": ("other", "Non-current Commitments to Purchase Non-controlling Interests"),
    "NonCurrentProvisionsAndOtherNonCurrentLiabilities": ("balance_sheet", "Non-current Provisions and Other Non-current Liabilities"),
    "OperatingFreeCashFlow": ("cash_flow", "Operating Free Cash Flow"),
    "OtherComprehensiveIncomeBeforeTaxGainsLossesOnRevaluationVineyardLand": ("other", "OCI before Tax - Vineyard Land Revaluation"),
    "OtherComprehensiveIncomeNetOfTaxChangeInValueOfTimeValueOfOptionsOtherComprehensiveIncomeNetOfTaxChangeInValueOfForwardElementsOfForwardContractsAndOtherComprehensiveIncomeNetOfTaxChangeInValueOfForeignCurrencyBasisSpreads": ("other", "OCI Net of Tax - Hedge Cost/Time Value Components"),
    "OtherComprehensiveIncomeNetOfTaxGainsLossesOnRevaluationVineyardLand": ("other", "OCI Net of Tax - Vineyard Land Revaluation"),
    "ProceedsFromChangesInOwnershipInterestsInSubsidiariesAndPaymentsFromChangesInOwnershipInterestsInSubsidiaries": ("cash_flow", "Proceeds/(Payments) from Changes in Ownership of Subsidiaries"),
    "ProfitLossFromOperatingActivitiesAfterShareOfProfitLossOfAssociatesAndJointVentures": ("income_statement", "Operating Profit after Share of Associates/JVs"),
    "ProfitLossFromOperatingActivitiesAfterShareOfProfitLossOfAssociatesAndJointVenturesInOperatingActivity": ("income_statement", "Operating Profit after Share of Associates/JVs (Operating Activity)"),
    "ProfitLossFromOperatingActivitiesRecurringIncludingShareOfProfitOfEquityAccountedInvestees": ("income_statement", "Recurring Operating Profit incl. Equity-accounted Investees"),
    "PurchaseAndProceedsFromSaleOfConsolidatedInvestmentsClassifiedAsInvestingActivities": ("cash_flow", "Purchase/(Sale) of Consolidated Investments"),
    "PurchaseAndProceedsFromSaleOfNonCurrentAvailableForSaleFinancialAssetsClassifiedAsInvestingActivities": ("cash_flow", "Purchase/(Sale) of Non-current AFS Financial Assets"),
    "ReclassificationAdjustmentsOnChangeInValueOfTimeValueOfOptionsBeforeTaxReclassificationAdjustmentsOnChangeInValueOfForwardElementsOfForwardContractsBeforeTaxAndReclassificationAdjustmentsOnChangeInValueOfForeignCurrencyBasisSpreadsBeforeTax": ("other", "OCI Reclassification - Hedge Cost/Time Value Components"),
    "ReclassificationAdjustmentsOnExchangeDifferencesOnTranslationBeforeTaxAndReclassificationAdjustmentsOnHedgesOfNetInvestmentsInForeignOperationsBeforeTax": ("other", "OCI Reclassification - Translation & Net Investment Hedges"),
    "ReclassificationAdjustmentsOnGainsLossesOnRevaluationToRetainedEarningsBeforeTaxVineyardLand": ("other", "OCI Reclassification - Vineyard Land Revaluation to Retained Earnings"),
    # --- essi: (Essity) extension tags ---
    "ActuarialGainsLossesOnDefinedBenefitPensionPlansContinuingOperations": ("other", "Actuarial Gains/Losses on Defined Benefit Pension Plans"),
    "AmortizationOfAcquisitionRelatedIntangibleAssetsExcludingItemsAffectingComparability": ("income_statement", "Amortization of Acquisition-related Intangibles (excl. IAC)"),
    "CapitalizedExpenditureToFulfillContractsWithCustomers": ("cash_flow", "Capitalized Expenditure to Fulfill Customer Contracts"),
    "CashFlowForThePeriodContinuingOperations": ("cash_flow", "Net Cash Flow for the Period (Continuing Operations)"),
    "ChangeInLiabilitiesRelatingToRestructuringProgramsEtc": ("cash_flow", "Change in Restructuring-related Liabilities"),
    "CostOfGoodsSoldExcludingItemsAffectingComparability": ("income_statement", "Cost of Goods Sold (excl. IAC)"),
    "GainsLossesFromHedgesOfNetInvestmentsInForeignOperationsContinuingOperations": ("other", "OCI - Hedges of Net Investments in Foreign Operations"),
    "GrossProfitExclIAC": ("income_statement", "Gross Profit (excl. IAC)"),
    "IncomeTaxAttributableToComponentsInOtherComprehensiveContinuingOperations": ("other", "Income Tax on OCI Components"),
    "ItemsAffectingComparabilityAmortizationOfAcquisitionRelatedIntangibleAssets": ("income_statement", "IAC - Amortization of Acquisition-related Intangibles"),
    "ItemsAffectingComparabilityCostOfGoodsSold": ("income_statement", "IAC - Cost of Goods Sold"),
    "ItemsAffectingComparabilitySalesGeneralAndAdministration": ("income_statement", "IAC - SG&A"),
    "OperatingProfitBeforeAmortizationOfAcquisitionRelatedIntangibleAssetEBITAExclIAC": ("income_statement", "EBITA excl. IAC"),
    "OperatingProfitBeforeAmortizationOfAcquisitionRelatedIntangibleAssetsEBITA": ("income_statement", "EBITA (Operating Profit before Amortization of Acquisition Intangibles)"),
    "OperatingProfitExclIAC": ("income_statement", "Operating Profit (excl. IAC)"),
    "OperatingProfitExcludingNonCashItems": ("income_statement", "Operating Profit (excl. Non-cash Items)"),
    "OtherComprehensiveIncomeForThePeriodNetOfTaxContinuingOperations": ("other", "Other Comprehensive Income, Net of Tax (Continuing Operations)"),
    "OtherComprehensiveIncomeForThePeriodNetOfTaxDiscontinuingOperations": ("other", "Other Comprehensive Income, Net of Tax (Discontinued Operations)"),
    "OtherFinancialItems": ("income_statement", "Other Financial Items"),
    "ProfitBeforeTaxExclIAC": ("income_statement", "Profit Before Tax (excl. IAC)"),
    "ProfitForThePeriodExclIACContinuingOperations": ("income_statement", "Profit for the Period (excl. IAC, Continuing Operations)"),
    "RevaluationEffectUponAcquisitionOfNon-controllingInterests": ("other", "Revaluation Effect on Acquisition of Non-controlling Interests"),
    "SalesGeneralAndAdministrationExcludingItemsAffectingComparability": ("income_statement", "SG&A (excl. IAC)"),
    "TotalAssetsContinuingOperations": ("balance_sheet", "Total Assets (Continuing Operations)"),
    "TotalItemsThatHaveBeenOrMayBeReclassifiedSubsequentlyToTheIncomeStatementContinuingOperations": ("other", "OCI Items Reclassifiable to P&L (Continuing Operations)"),
    "TotalItemsThatHaveBeenOrMayBeReclassifiedSubsequentlyToTheIncomeStatementDiscontinuingOperations": ("other", "OCI Items Reclassifiable to P&L (Discontinued Operations)"),
    "TotalItemsThatWillNotBeReclassifiedToTheIncomeStatementContinuingOperations": ("other", "OCI Items Not Reclassifiable to P&L (Continuing Operations)"),
    "TotalItemsThatWillNotBeReclassifiedToTheIncomeStatementDiscontinuedOperations": ("other", "OCI Items Not Reclassifiable to P&L (Discontinued Operations)"),
    "TotalLiabilitiesContinuingOperations": ("balance_sheet", "Total Liabilities (Continuing Operations)"),
    "TranslationDifferencesInForeignOperationsContinuingOperations": ("other", "Translation Differences in Foreign Operations (OCI)"),
    # --- shel: (Shell) extension tags ---
    "AdjustmentsForIncreaseDecreaseInDerivativeFinancialInstruments": ("cash_flow", "Adjustments for Change in Derivative Financial Instruments"),
    "AmountsTransferredFromOtherComprehensiveIncomeToProfit": ("other", "OCI Reclassification to Profit/Loss"),
    "CashFlowsFromUsedInIncreaseDecreaseInDerivativeFinancialInstruments": ("cash_flow", "Change in Derivative Financial Instruments"),
    "CashOutflowForTotalCashCapitalExpenditure": ("cash_flow", "Total Cash Capital Expenditure"),
    "ChangeInNonControllingInterest": ("other", "Change in Non-controlling Interest"),
    "ComprehensiveIncomeIncludingRemeasurementOfInterestInJointVenture": ("income_statement", "Comprehensive Income incl. Remeasurement of JV Interest"),
    "InterestAndOtherIncome": ("income_statement", "Interest and Other Income"),
    "NetGainsOnSaleAndRevaluationOfNonCurrentAssetsAndBusinesses": ("income_statement", "Net Gains on Sale/Revaluation of Non-current Assets and Businesses"),
    "NetIncreaseDecreaseInDebtWithMaturityPeriodWithinThreeMonths": ("cash_flow", "Net Change in Short-term Debt (<3 Months)"),
    "NetPurchasesAndDividendsReceivedSharesHeldInTrustClassifiedAsFinancingActivities": ("cash_flow", "Net Purchases/Dividends on Shares Held in Trust"),
    "OtherInflowsOfCashClassifiedAsInvestingActivities": ("cash_flow", "Other Investing Cash Inflows"),
    "OtherOutflowsOfCashClassifiedAsInvestingActivities": ("cash_flow", "Other Investing Cash Outflows"),
    "ProceedsFromJointVenturesAndAssociatesFromSaleCapitalReductionAndRepaymentOfLongTermLoans": ("cash_flow", "Proceeds from JVs/Associates (Sale, Capital Reduction, Loan Repayment)"),
    "RevenueAndOtherIncome": ("income_statement", "Revenue and Other Income"),
    "TangibleExplorationAndEvaluationAssetsAmountChargedToExpense": ("income_statement", "Exploration & Evaluation Assets Charged to Expense"),
    # --- loreal: extra tag (different from the earlier IncidenceDesVariationsDePerimetre) ---
    "VariationsDePerimetre": ("cash_flow", "Changes in Scope of Consolidation"),
    # --- new standard ifrs-full: tags ---
    "AdjustedWeightedAverageShares": ("income_statement", "Adjusted Weighted Average Shares"),
    "AdjustmentsForDecreaseIncreaseInInventories": ("cash_flow", "Adjustments for Decrease/(Increase) in Inventories"),
    "AdjustmentsForDecreaseIncreaseInTradeAndOtherReceivables": ("cash_flow", "Adjustments for Decrease/(Increase) in Trade Receivables"),
    "AdjustmentsForDepreciationAndAmortisationExpenseAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss": ("cash_flow", "Depreciation, Amortization and Impairment Adjustments"),
    "AdjustmentsForIncreaseDecreaseInEmployeeBenefitLiabilities": ("cash_flow", "Adjustments for Increase/(Decrease) in Employee Benefit Liabilities"),
    "AdjustmentsForIncreaseDecreaseInTradeAndOtherPayables": ("cash_flow", "Adjustments for Increase/(Decrease) in Trade Payables"),
    "AdjustmentsForInterestExpense": ("cash_flow", "Adjustments for Interest Expense"),
    "AdjustmentsForProvisions": ("cash_flow", "Adjustments for Provisions"),
    "AdjustmentsToReconcileProfitLossOtherThanChangesInWorkingCapital": ("cash_flow", "Adjustments to Reconcile Profit/Loss (excl. Working Capital)"),
    "AdministrativeExpense": ("income_statement", "Administrative Expense"),
    "AmountRemovedFromReserveOfCashFlowHedgesAndIncludedInInitialCostOrOtherCarryingAmountOfNonfinancialAssetLiabilityOrFirmCommitmentForWhichFairValueHedgeAccountingIsApplied": ("other", "Cash Flow Hedge Reserve Reclassified to Asset/Liability Cost"),
    "BasicEarningsLossPerShareFromContinuingOperations": ("income_statement", "Basic EPS from Continuing Operations"),
    "BasicEarningsLossPerShareFromDiscontinuedOperations": ("income_statement", "Basic EPS from Discontinued Operations"),
    "CancellationOfTreasuryShares": ("other", "Cancellation of Treasury Shares (Equity Movement)"),
    "CashAndCashEquivalents": ("balance_sheet", "Cash and Cash Equivalents"),
    "CashFlowsFromLosingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities": ("cash_flow", "Cash Flows from Losing Control of Subsidiaries"),
    "CashFlowsFromUsedInDecreaseIncreaseInShorttermDepositsAndInvestments": ("cash_flow", "Change in Short-term Deposits and Investments"),
    "CashFlowsFromUsedInFinancingActivitiesContinuingOperations": ("cash_flow", "Cash Flow from Financing Activities (Continuing Operations)"),
    "CashFlowsFromUsedInFinancingActivitiesDiscontinuedOperations": ("cash_flow", "Cash Flow from Financing Activities (Discontinued Operations)"),
    "CashFlowsFromUsedInInvestingActivitiesContinuingOperations": ("cash_flow", "Cash Flow from Investing Activities (Continuing Operations)"),
    "CashFlowsFromUsedInInvestingActivitiesDiscontinuedOperations": ("cash_flow", "Cash Flow from Investing Activities (Discontinued Operations)"),
    "CashFlowsFromUsedInOperatingActivitiesContinuingOperations": ("cash_flow", "Cash Flow from Operating Activities (Continuing Operations)"),
    "CashFlowsFromUsedInOperatingActivitiesDiscontinuedOperations": ("cash_flow", "Cash Flow from Operating Activities (Discontinued Operations)"),
    "CashFlowsUsedInObtainingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities": ("cash_flow", "Cash Used in Obtaining Control of Subsidiaries"),
    "ComprehensiveIncome": ("income_statement", "Total Comprehensive Income"),
    "ComprehensiveIncomeFromContinuingOperations": ("income_statement", "Comprehensive Income (Continuing Operations)"),
    "ComprehensiveIncomeFromDiscontinuedOperations": ("income_statement", "Comprehensive Income (Discontinued Operations)"),
    "CurrentAssetsOtherThanAssetsOrDisposalGroupsClassifiedAsHeldForSaleOrAsHeldForDistributionToOwners": ("balance_sheet", "Current Assets (excl. Held-for-sale)"),
    "CurrentLiabilitiesOtherThanLiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale": ("balance_sheet", "Current Liabilities (excl. Held-for-sale)"),
    "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss": ("cash_flow", "Depreciation, Amortization and Impairment (P&L)"),
    "DilutedEarningsLossPerShareFromContinuingOperations": ("income_statement", "Diluted EPS from Continuing Operations"),
    "DilutedEarningsLossPerShareFromDiscontinuedOperations": ("income_statement", "Diluted EPS from Discontinued Operations"),
    "DistributionCosts": ("income_statement", "Distribution Costs"),
    "DividendsPaid": ("cash_flow", "Dividends Paid"),
    "DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities": ("cash_flow", "Dividends Paid to Parent's Equity Holders"),
    "DividendsPaidToNoncontrollingInterestsClassifiedAsFinancingActivities": ("cash_flow", "Dividends Paid to Non-controlling Interests"),
    "DividendsProposedOrDeclaredBeforeFinancialStatementsAuthorisedForIssueButNotRecognisedAsDistributionToOwners": ("other", "Dividends Proposed, Not Yet Recognized"),
    "DividendsReceivedClassifiedAsInvestingActivities": ("cash_flow", "Dividends Received"),
    "DividendsRecognisedAsDistributionsToOwnersOfParent": ("other", "Dividends Recognized as Distributions to Owners"),
    "DividendsRecognisedAsDistributionsToOwnersPerShare": ("other", "Dividends per Share"),
    "ExpenseArisingFromExplorationForAndEvaluationOfMineralResources": ("income_statement", "Exploration & Evaluation Expense"),
    "FinanceCosts": ("income_statement", "Finance Costs"),
    "FinanceIncome": ("income_statement", "Finance Income"),
    "FinanceIncomeCost": ("income_statement", "Finance Income/(Cost), Net"),
    "GainsLossesOnCashFlowHedgesBeforeTax": ("other", "OCI before Tax - Cash Flow Hedges (Gains/Losses)"),
    "GainsLossesOnCashFlowHedgesNetOfTax": ("other", "OCI Net of Tax - Cash Flow Hedges (Gains/Losses)"),
    "IncomeFromContinuingOperationsAttributableToOwnersOfParent": ("income_statement", "Income from Continuing Operations Attributable to Owners"),
    "IncomeFromDiscontinuedOperationsAttributableToOwnersOfParent": ("income_statement", "Income from Discontinued Operations Attributable to Owners"),
    "IncomeTaxRelatingToChangesInRevaluationSurplusOfOtherComprehensiveIncome": ("other", "Income Tax on Revaluation Surplus (OCI)"),
    "IncomeTaxRelatingToComponentsOfOtherComprehensiveIncomeThatWillBeReclassifiedToProfitOrLoss": ("other", "Income Tax on OCI Items Reclassifiable to P&L"),
    "IncomeTaxRelatingToExchangeDifferencesOnTranslationOfForeignOperationsAndHedgesOfNetInvestmentsInForeignOperationsIncludedInOtherComprehensiveIncome": ("other", "Income Tax on Translation & Net Investment Hedges (OCI)"),
    "IncomeTaxesPaidClassifiedAsOperatingActivities": ("cash_flow", "Income Taxes Paid (Operating)"),
    "IncomeTaxesPaidRefundClassifiedAsInvestingActivities": ("cash_flow", "Income Taxes Paid (Investing)"),
    "IncomeTaxesPaidRefundClassifiedAsOperatingActivities": ("cash_flow", "Income Taxes Paid (Operating)"),
    "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges": ("cash_flow", "Increase/(Decrease) in Cash Before FX Effect"),
    "IncreaseDecreaseInCashAndCashEquivalentsDiscontinuedOperations": ("cash_flow", "Increase/(Decrease) in Cash (Discontinued Operations)"),
    "IncreaseDecreaseThroughAcquisitionOfSubsidiary": ("other", "Change in Equity from Acquisition of Subsidiary"),
    "IncreaseDecreaseThroughChangeInEquityOfSubsidiaries": ("other", "Change in Equity of Subsidiaries"),
    "IncreaseDecreaseThroughChangesInOwnershipInterestsInSubsidiariesThatDoNotResultInLossOfControl": ("other", "Change in Equity from Ownership Changes in Subsidiaries (No Loss of Control)"),
    "IncreaseDecreaseThroughSharebasedPaymentTransactions": ("other", "Change in Equity from Share-based Payments"),
    "IncreaseDecreaseThroughTransfersAndOtherChangesEquity": ("other", "Other Transfers/Changes in Equity"),
    "IncreaseDecreaseThroughTreasuryShareTransactions": ("other", "Change in Equity from Treasury Share Transactions"),
    "InterestExpense": ("income_statement", "Interest Expense"),
    "InterestExpenseOnLeaseLiabilities": ("income_statement", "Interest Expense on Lease Liabilities"),
    "InterestPaidClassifiedAsFinancingActivities": ("cash_flow", "Interest Paid (Financing)"),
    "InterestPaidClassifiedAsInvestingActivities": ("cash_flow", "Interest Paid (Investing)"),
    "InterestReceivedClassifiedAsInvestingActivities": ("cash_flow", "Interest Received (Investing)"),
    "InterestReceivedClassifiedAsOperatingActivities": ("cash_flow", "Interest Received (Operating)"),
    "IssueOfEquity": ("other", "Issue of Equity"),
    "LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale": ("balance_sheet", "Liabilities in Disposal Groups Held for Sale"),
    "NoncurrentAssetsOrDisposalGroupsClassifiedAsHeldForSale": ("balance_sheet", "Non-current Assets/Disposal Groups Held for Sale"),
    "NoncurrentFinancialAssetsAvailableforsale": ("balance_sheet", "Non-current Available-for-sale Financial Assets"),
    "NoncurrentInvestmentsOtherThanInvestmentsAccountedForUsingEquityMethod": ("balance_sheet", "Non-current Investments (excl. Equity-method)"),
    "NoncurrentRecognisedAssetsDefinedBenefitPlan": ("balance_sheet", "Non-current Defined Benefit Plan Assets"),
    "NoncurrentRecognisedLiabilitiesDefinedBenefitPlan": ("balance_sheet", "Non-current Defined Benefit Plan Liabilities"),
    "NumberOfSharesOutstanding": ("other", "Number of Shares Outstanding"),
    "OperatingExpense": ("income_statement", "Operating Expense"),
    "OtherAdjustmentsToReconcileProfitLoss": ("cash_flow", "Other Adjustments to Reconcile Profit/Loss"),
    "OtherComprehensiveIncomeBeforeTaxHedgesOfNetInvestmentsInForeignOperations": ("other", "OCI before Tax - Hedges of Net Investments in Foreign Operations"),
    "OtherComprehensiveIncomeNetOfTaxCashFlowHedges": ("other", "OCI Net of Tax - Cash Flow Hedges"),
    "OtherComprehensiveIncomeNetOfTaxChangeInValueOfForeignCurrencyBasisSpreads": ("other", "OCI Net of Tax - Foreign Currency Basis Spreads"),
    "OtherComprehensiveIncomeNetOfTaxExchangeDifferencesOnTranslation": ("other", "OCI Net of Tax - Currency Translation Differences"),
    "OtherComprehensiveIncomeNetOfTaxExchangeDifferencesOnTranslationOfForeignOperationsAndHedgesOfNetInvestmentsInForeignOperations": ("other", "OCI Net of Tax - Translation & Net Investment Hedges"),
    "OtherComprehensiveIncomeNetOfTaxFinancialAssetsMeasuredAtFairValueThroughOtherComprehensiveIncome": ("other", "OCI Net of Tax - FVOCI Financial Assets"),
    "OtherComprehensiveIncomeNetOfTaxGainsLossesFromInvestmentsInEquityInstruments": ("other", "OCI Net of Tax - Gains/Losses on Equity Instruments"),
    "OtherComprehensiveIncomeNetOfTaxGainsLossesOnRemeasurementsOfDefinedBenefitPlans": ("other", "OCI Net of Tax - Remeasurements of Defined Benefit Plans"),
    "OtherComprehensiveIncomeNetOfTaxHedgesOfNetInvestmentsInForeignOperations": ("other", "OCI Net of Tax - Hedges of Net Investments in Foreign Operations"),
    "OtherComprehensiveIncomeThatWillBeReclassifiedToProfitOrLossNetOfTax": ("other", "OCI That Will Be Reclassified to P&L, Net of Tax"),
    "OtherComprehensiveIncomeThatWillNotBeReclassifiedToProfitOrLossNetOfTax": ("other", "OCI That Will Not Be Reclassified to P&L, Net of Tax"),
    "OtherCurrentAssets": ("balance_sheet", "Other Current Assets"),
    "OtherCurrentFinancialAssets": ("balance_sheet", "Other Current Financial Assets"),
    "OtherCurrentFinancialLiabilities": ("balance_sheet", "Other Current Financial Liabilities"),
    "OtherCurrentPayables": ("balance_sheet", "Other Current Payables"),
    "OtherCurrentReceivables": ("balance_sheet", "Other Current Receivables"),
    "OtherExpenseByNature": ("income_statement", "Other Expense by Nature"),
    "OtherInflowsOutflowsOfCashClassifiedAsOperatingActivities": ("cash_flow", "Other Operating Cash Flows"),
    "OtherNoncurrentAssets": ("balance_sheet", "Other Non-current Assets"),
    "OtherNoncurrentFinancialLiabilities": ("balance_sheet", "Other Non-current Financial Liabilities"),
    "OtherNoncurrentLiabilities": ("balance_sheet", "Other Non-current Liabilities"),
    "OtherNoncurrentNonfinancialAssets": ("balance_sheet", "Other Non-current Non-financial Assets"),
    "OtherOperatingIncomeExpense": ("income_statement", "Other Operating Income/(Expense)"),
    "OtherReserves": ("balance_sheet", "Other Reserves"),
    "OtherShorttermProvisions": ("balance_sheet", "Other Short-term Provisions"),
    "PaymentsToAcquireOrRedeemEntitysShares": ("cash_flow", "Payments to Acquire/Redeem Own Shares"),
    "ProceedsFromBorrowingsClassifiedAsFinancingActivities": ("cash_flow", "Proceeds from Borrowings"),
    "ProceedsFromDisposalsOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets": ("cash_flow", "Proceeds from Disposals of PP&E, Intangibles and Other Non-current Assets"),
    "ProceedsFromSalesOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": ("cash_flow", "Proceeds from Sale of PP&E"),
    "ProceedsFromSalesOrMaturityOfFinancialInstrumentsClassifiedAsInvestingActivities": ("cash_flow", "Proceeds from Sale/Maturity of Financial Instruments"),
    "ProfitLossAttributableToNoncontrollingInterests": ("income_statement", "Profit/(Loss) Attributable to Non-controlling Interests"),
    "ProfitLossAttributableToOwnersOfParent": ("income_statement", "Net Profit Attributable to Owners of Parent"),
    "ProfitLossBeforeTax": ("income_statement", "Profit Before Tax"),
    "ProfitLossFromContinuingOperationsAttributableToNoncontrollingInterests": ("income_statement", "Profit from Continuing Operations Attributable to NCI"),
    "ProfitLossFromDiscontinuedOperationsAttributableToNoncontrollingInterests": ("income_statement", "Profit from Discontinued Operations Attributable to NCI"),
    "PurchaseOfFinancialInstrumentsClassifiedAsInvestingActivities": ("cash_flow", "Purchase of Financial Instruments"),
    "PurchaseOfInterestsInInvestmentsAccountedForUsingEquityMethod": ("cash_flow", "Purchase of Equity-method Investments"),
    "PurchaseOfOtherLongtermAssetsClassifiedAsInvestingActivities": ("cash_flow", "Purchase of Other Long-term Assets"),
    "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets": ("cash_flow", "Purchase of PP&E, Intangibles and Other Non-current Assets"),
    "PurchaseOfTreasuryShares": ("cash_flow", "Purchase of Treasury Shares"),
    "RawMaterialsAndConsumablesUsed": ("income_statement", "Raw Materials and Consumables Used"),
    "ReclassificationAdjustmentsOnCashFlowHedgesBeforeTax": ("other", "OCI Reclassification - Cash Flow Hedges"),
    "RepaymentsOfBorrowingsClassifiedAsFinancingActivities": ("cash_flow", "Repayments of Borrowings"),
    "RetainedEarnings": ("balance_sheet", "Retained Earnings"),
    "ShareOfOtherComprehensiveIncomeOfAssociatesAndJointVenturesAccountedForUsingEquityMethodThatWillBeReclassifiedToProfitOrLossNetOfTax": ("other", "Share of OCI of Associates/JVs, Reclassifiable to P&L"),
    "ShareOfOtherComprehensiveIncomeOfAssociatesAndJointVenturesAccountedForUsingEquityMethodThatWillNotBeReclassifiedToProfitOrLossNetOfTax": ("other", "Share of OCI of Associates/JVs, Not Reclassifiable to P&L"),
    "TradeAndOtherCurrentPayables": ("balance_sheet", "Trade and Other Current Payables"),
    "TradeAndOtherCurrentReceivables": ("balance_sheet", "Trade and Other Current Receivables"),
    "UndatedSubordinatedLiabilities": ("balance_sheet", "Undated Subordinated Liabilities"),
    "WeightedAverageShares": ("income_statement", "Weighted Average Shares"),
}

with open("data/mappings/pooled_new_concepts.yaml") as f:
    draft = yaml.safe_load(f)

with open("data/mappings/ifrs_concepts_v0.yaml") as f:
    existing = yaml.safe_load(f)

existing_tags = set()
for stmt, concepts in existing.items():
    for name, info in concepts.items():
        existing_tags.update(info["xbrl_tags"])

added = 0
skipped_already_covered = 0
missing_classification = []

for entry in draft["unmapped_concepts_to_review"]:
    tag = entry["xbrl_tag"]
    if tag in existing_tags:
        skipped_already_covered += 1
        continue

    short = tag.split(":")[-1]
    if short not in new_classification:
        missing_classification.append(tag)
        continue

    stmt, label = new_classification[short]
    key = entry["suggested_key"]
    if len(key) > 60:
        key = key[:57] + "_etc"
    if key in existing[stmt]:
        key = key + "_v2"
    existing[stmt][key] = {"display_label": label, "xbrl_tags": [tag]}
    added += 1

print(f"Already covered (no action needed): {skipped_already_covered}")
print(f"Newly added: {added}")
if missing_classification:
    print(f"MISSING classification for {len(missing_classification)} tags:")
    for t in missing_classification:
        print(f"  {t}")

with open("data/mappings/ifrs_concepts_v0.yaml", "w", encoding="utf-8") as f:
    yaml.dump(existing, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

print("\nUpdated data/mappings/ifrs_concepts_v0.yaml")
for stmt, items in existing.items():
    print(f"  {stmt}: {len(items)} concepts total")
