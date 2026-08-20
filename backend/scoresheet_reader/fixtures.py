from __future__ import annotations

from uuid import uuid4

from .models import (
    DocumentStatus,
    FinalScore,
    FoulCode,
    FoulEntry,
    Header,
    OfficialEntry,
    ParticipationStatus,
    PeriodScore,
    PlayerEntry,
    PostFoulMarker,
    ScoreEvent,
    ScoresheetDocument,
    SignaturePresence,
    TeamEntry,
    TeamFoulPeriod,
    TeamSide,
    TimeoutEntry,
)


def _players(side: TeamSide) -> list[PlayerEntry]:
    prefix = "甲" if side == TeamSide.A else "乙"
    jerseys = ["4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"]
    players = [
        PlayerEntry(
            row=index,
            license_number=f"{100 + index}",
            name=f"示例{prefix}{index:02d}",
            jersey_number=jersey,
            captain=index == 4,
            participation=(
                ParticipationStatus.STARTER
                if index <= 5
                else ParticipationStatus.SUBSTITUTE
                if index <= 10
                else ParticipationStatus.NONE
            ),
        )
        for index, jersey in enumerate(jerseys, start=1)
    ]
    if side == TeamSide.A:
        players[0].fouls = [FoulEntry(slot=1, code=FoulCode.P, period=1)]
        players[1].fouls = [
            FoulEntry(slot=1, code=FoulCode.P, free_throws=2, period=1),
            FoulEntry(slot=2, code=FoulCode.T, period=2),
        ]
        players[2].fouls = [
            FoulEntry(slot=1, code=FoulCode.U, free_throws=2, period=3),
            FoulEntry(slot=2, code=FoulCode.P, cancelled=True, period=4),
        ]
        players[3].fouls = [FoulEntry(slot=1, code=FoulCode.D, free_throws=2, period=3)]
        players[4].fouls = [FoulEntry(slot=1, code=FoulCode.P, period=2)]
    else:
        players[0].fouls = [FoulEntry(slot=1, code=FoulCode.P, period=1)]
        players[1].fouls = [FoulEntry(slot=1, code=FoulCode.P, period=2)]
        players[2].fouls = [FoulEntry(slot=1, code=FoulCode.T, free_throws=1, period=3)]
        players[3].fouls = [FoulEntry(slot=1, code=FoulCode.U, period=4)]
    return players


def synthetic_document(document_id: str | None = None) -> ScoresheetDocument:
    team_a = TeamEntry(
        side=TeamSide.A,
        name="示例学院甲",
        players=_players(TeamSide.A),
        timeouts=[
            TimeoutEntry(scope="H1", slot=1, minute=7),
            TimeoutEntry(scope="H2", slot=1, minute=6),
            TimeoutEntry(scope="H2", slot=2, minute=2),
        ],
        team_fouls=[
            TeamFoulPeriod(period=1, count=2),
            TeamFoulPeriod(period=2, count=2),
            TeamFoulPeriod(period=3, count=2),
            TeamFoulPeriod(period=4, count=1),
        ],
        coach_fouls=[
            FoulEntry(slot=1, code=FoulCode.C, period=2),
            FoulEntry(slot=2, code=FoulCode.B, free_throws=2, period=3),
            FoulEntry(slot=3, code=FoulCode.C, period=3),
        ],
        coach_post_foul_markers=[
            PostFoulMarker(slot=1, code=FoulCode.GD, period=3),
            PostFoulMarker(slot=2, code=FoulCode.F, period=3),
        ],
        assistant_coach_fouls=[
            FoulEntry(slot=1, code=FoulCode.D, free_throws=2, period=3),
            FoulEntry(slot=2, code=FoulCode.F, period=3),
            FoulEntry(slot=3, code=FoulCode.F, period=3),
        ],
        head_coach="示例教练甲",
        assistant_coach="示例助教甲",
    )
    team_b = TeamEntry(
        side=TeamSide.B,
        name="示例学院乙",
        players=_players(TeamSide.B),
        timeouts=[TimeoutEntry(scope="H1", slot=1, minute=5)],
        team_fouls=[
            TeamFoulPeriod(period=1, count=1),
            TeamFoulPeriod(period=2, count=1),
            TeamFoulPeriod(period=3, count=1),
            TeamFoulPeriod(period=4, count=1),
        ],
        head_coach="示例教练乙",
        assistant_coach="示例助教乙",
    )

    events = [
        ScoreEvent(
            sequence=1,
            team="A",
            period=1,
            points=2,
            cumulative_score=2,
            scorer_jersey="4",
            mark="diagonal",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=2,
            team="B",
            period=1,
            points=2,
            cumulative_score=2,
            scorer_jersey="4",
            mark="diagonal",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=3,
            team="A",
            period=1,
            points=1,
            cumulative_score=3,
            scorer_jersey="5",
            mark="filled_dot",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=4,
            team="B",
            period=1,
            points=1,
            cumulative_score=3,
            scorer_jersey="5",
            mark="filled_dot",
            boundary="period_end",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=5,
            team="A",
            period=1,
            points=3,
            cumulative_score=6,
            scorer_jersey="7",
            mark="diagonal",
            scorer_circled=True,
            boundary="period_end",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=6,
            team="A",
            period=2,
            points=2,
            cumulative_score=8,
            scorer_jersey="8",
            mark="diagonal",
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=7,
            team="B",
            period=2,
            points=3,
            cumulative_score=6,
            scorer_jersey="6",
            mark="diagonal",
            scorer_circled=True,
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=8,
            team="A",
            period=2,
            points=2,
            cumulative_score=10,
            scorer_jersey="9",
            mark="diagonal",
            boundary="period_end",
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=9,
            team="B",
            period=2,
            points=2,
            cumulative_score=8,
            scorer_jersey="7",
            mark="diagonal",
            boundary="period_end",
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=10,
            team="A",
            period=3,
            points=3,
            cumulative_score=13,
            scorer_jersey="10",
            mark="diagonal",
            scorer_circled=True,
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=11,
            team="B",
            period=3,
            points=2,
            cumulative_score=10,
            scorer_jersey="8",
            mark="diagonal",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=12,
            team="A",
            period=3,
            points=2,
            cumulative_score=15,
            scorer_jersey="11",
            mark="diagonal",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=13,
            team="B",
            period=3,
            points=2,
            cumulative_score=12,
            scorer_jersey="9",
            mark="diagonal",
            boundary="period_end",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=14,
            team="A",
            period=3,
            points=1,
            cumulative_score=16,
            scorer_jersey="12",
            mark="filled_dot",
            boundary="period_end",
            ink_role="q1_q3",
        ),
        ScoreEvent(
            sequence=15,
            team="B",
            period=4,
            points=1,
            cumulative_score=13,
            scorer_jersey="10",
            mark="filled_dot",
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=16,
            team="A",
            period=4,
            points=2,
            cumulative_score=18,
            scorer_jersey="13",
            mark="diagonal",
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=17,
            team="B",
            period=4,
            points=2,
            cumulative_score=15,
            scorer_jersey="11",
            mark="diagonal",
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=18,
            team="A",
            period=4,
            points=3,
            cumulative_score=21,
            scorer_jersey="14",
            mark="diagonal",
            scorer_circled=True,
            boundary="game_end",
            ink_role="q2_q4_ot",
        ),
        ScoreEvent(
            sequence=19,
            team="B",
            period=4,
            points=3,
            cumulative_score=18,
            scorer_jersey="12",
            mark="diagonal",
            scorer_circled=True,
            boundary="game_end",
            ink_role="q2_q4_ot",
        ),
    ]

    return ScoresheetDocument(
        id=document_id or str(uuid4()),
        status=DocumentStatus.NEEDS_REVIEW,
        header=Header(
            competition="合成测试赛",
            game_number="SYN-001",
            date="2026-08-18",
            scheduled_time="14:00",
            venue="测试球馆",
            crew_chief="示例主裁",
            umpire_1="示例副裁一",
            umpire_2="示例副裁二",
        ),
        teams=[team_a, team_b],
        score_events=events,
        stated_period_scores=[
            PeriodScore(period=1, team_a=6, team_b=3),
            PeriodScore(period=2, team_a=4, team_b=5),
            PeriodScore(period=3, team_a=6, team_b=4),
            PeriodScore(period=4, team_a=5, team_b=6),
        ],
        final_score=FinalScore(
            team_a=21,
            team_b=18,
            winner_name="示例学院甲",
            ended_at="15:28",
        ),
        officials=[
            OfficialEntry(role="scorer", name="示例记录员", signature=SignaturePresence.PRESENT),
            OfficialEntry(
                role="assistant_scorer", name="示例助理记录员", signature=SignaturePresence.PRESENT
            ),
            OfficialEntry(role="timer", name="示例计时员", signature=SignaturePresence.PRESENT),
            OfficialEntry(
                role="shot_clock_operator", name="示例24秒员", signature=SignaturePresence.PRESENT
            ),
            OfficialEntry(role="crew_chief", name="示例主裁", signature=SignaturePresence.PRESENT),
            OfficialEntry(role="umpire_1", name="示例副裁一", signature=SignaturePresence.PRESENT),
            OfficialEntry(role="umpire_2", name="示例副裁二", signature=SignaturePresence.UNCLEAR),
            OfficialEntry(role="protest_captain", name="", signature=SignaturePresence.ABSENT),
        ],
    )
